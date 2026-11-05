"""スライドのリンク判定と、記事入力用 PDF の読み込み。"""
from datetime import timedelta
from html.parser import HTMLParser
import logging
import re
import tempfile
import time
from urllib.parse import unquote, urlsplit

import requests
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from event.form_validators import MAX_PDF_SIZE_BYTES
from event.models import EventDetail, SLIDE_FETCH_FIELDS, article_generation_request_values, validate_pdf_file
from event.services.pdf_worker import run_pdf_worker
from event.services.media_service import ensure_pdf_thumbnail

logger = logging.getLogger(__name__)

SLIDE_SERVICE_LABELS = {
    'speakerdeck': 'Speaker Deck',
    'slideshare': 'SlideShare',
    'docswell': 'Docswell',
    'google_slides': 'Google スライド',
}
SERVICE_HOSTS = {
    'speakerdeck.com': 'speakerdeck',
    'slideshare.net': 'slideshare',
    'www.slideshare.net': 'slideshare',
    'docswell.com': 'docswell',
    'www.docswell.com': 'docswell',
    'docs.google.com': 'google_slides',
}
USER_AGENT = 'vrc-ta-hub/1.0 (+https://vrc-ta-hub.com/)'
PDF_PATH = re.compile(r'/presentations/[0-9A-Za-z]+/(?P<name>[^/\\]+\.pdf)')
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_ATTEMPTS = 3
CLAIM_LEASE = timedelta(minutes=5)
FETCH_TIMEOUT = (5, 15)
# FETCH_TIMEOUT は通信の待ち時間だけを見る。少しずつ届く応答で記事生成を待たせないよう、全体にも締切を置く。
FETCH_DEADLINE_SECONDS = 45

FETCH_ERROR_MESSAGES = {
    'no_pdf_link': 'ページに PDF のダウンロードリンクがありませんでした。',
    'page_unavailable': 'リンク先のページを開けませんでした。',
    'too_large': 'ファイルのサイズが上限を超えています。',
    'invalid_pdf': '有効な PDF を読み込めませんでした。',
    'network': '通信できませんでした。',
    'internal': '読み込み中に問題が起きました。',
}


def _parse_url(url):
    try:
        parsed = urlsplit(url or '')
        # 明示ポート（:443 も含む）と userinfo は許可しない。
        if parsed.scheme not in ('http', 'https') or parsed.netloc.lower() != parsed.hostname:
            return None
        return parsed
    except (ValueError, AttributeError):
        return None


def slide_service(url):
    parsed = _parse_url(url)
    return SERVICE_HOSTS.get(parsed.hostname) if parsed else None


def is_importable(url):
    parsed = _parse_url(url)
    if not parsed:
        return False
    # 末尾の / は同じページを指すが、そのまま読むと転送になる。読む時は canonical_page_url を使う。
    path = parsed.path.removesuffix('/')
    return bool(
        parsed.scheme == 'https' and parsed.hostname == 'speakerdeck.com'
        and len(path.split('/')) == 3 and all(path.split('/')[1:])
        and not any(c in path for c in ('%', '\\'))
        and all(part not in ('.', '..') for part in path.split('/'))
    )


def canonical_page_url(url):
    return f"https://speakerdeck.com{urlsplit(url).path.removesuffix('/')}"


def _is_pdf_url(url):
    parsed = _parse_url(url)
    if not parsed or parsed.scheme != 'https' or parsed.hostname != 'files.speakerdeck.com' or parsed.query:
        return False
    # 日本語のファイル名は %XX で届くので、デコードしても階層が増えないことを確かめる。
    match = PDF_PATH.fullmatch(parsed.path)
    return bool(match) and not any(c in unquote(match['name']) for c in ('/', '\\', '\0'))


class SlideFetchError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _PdfLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.pdf_url = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'a' and attributes.get('title') == 'Download PDF' and self.pdf_url is None:
            href = attributes.get('href')
            if _is_pdf_url(href):
                self.pdf_url = href


def _download(url, max_bytes, deadline):
    """転送を追わず、展開後の読み取り量にも上限を掛ける。"""
    try:
        with requests.get(
            url, headers={'User-Agent': USER_AGENT}, timeout=FETCH_TIMEOUT,
            allow_redirects=False, stream=True,
        ) as response:
            if response.status_code >= 500:
                raise SlideFetchError('network')
            if response.status_code != 200:
                raise SlideFetchError('page_unavailable')
            length = response.headers.get('Content-Length')
            if length and length.isdigit() and int(length) > max_bytes:
                raise SlideFetchError('too_large')
            body = bytearray()
            for chunk in response.iter_content(chunk_size=16 * 1024):
                if time.monotonic() > deadline:
                    raise SlideFetchError('network')
                if len(body) + len(chunk) > max_bytes:
                    raise SlideFetchError('too_large')
                body.extend(chunk)
            return bytes(body)
    except requests.RequestException:
        # リクエスト例外の本文は URL を含むため記録しない。
        raise SlideFetchError('network') from None


def fetch_slide_pdf(url):
    """許可したページと、そのページの Download PDF リンクだけを読む。"""
    if not is_importable(url):
        raise SlideFetchError('page_unavailable')
    deadline = time.monotonic() + FETCH_DEADLINE_SECONDS
    html = _download(canonical_page_url(url), MAX_HTML_BYTES, deadline)
    parser = _PdfLinkParser()
    parser.feed(html.decode('utf-8', errors='replace'))
    if not parser.pdf_url:
        raise SlideFetchError('no_pdf_link')
    data = _download(parser.pdf_url, MAX_PDF_SIZE_BYTES, deadline)
    pdf = ContentFile(data, name='source.pdf')
    try:
        validate_pdf_file(pdf)
        with tempfile.NamedTemporaryFile(suffix='.pdf') as temporary:
            temporary.write(data)
            temporary.flush()
            page_count = int(run_pdf_worker('page_count', temporary.name))
        if not page_count:
            raise ValueError('empty')
    except Exception:
        # pypdf の例外は種類が複数あるため、短い理由コードに揃える。
        raise SlideFetchError('invalid_pdf') from None
    return pdf, page_count


def reset_slide_fetch(detail):
    """読み込みの印を外す。ファイルの削除は呼び出し側で行う。"""
    source_url = detail.slide_fetch_url if detail.slide_source_file else ''
    detail.slide_fetch_status = ''
    detail.slide_fetch_error = ''
    detail.slide_fetch_url = source_url
    detail.slide_fetched_at = None
    detail.slide_source_page_count = None
    detail.slide_fetch_started_at = None
    detail.slide_fetch_attempts = 0
    detail.slide_generate_requested = False


def mark_slide_fetch_pending(detail):
    generate_requested = detail.slide_generate_requested
    reset_slide_fetch(detail)
    detail.slide_generate_requested = generate_requested
    detail.slide_fetch_status = EventDetail.SlideFetchStatus.PENDING
    detail.slide_fetch_url = detail.slide_url


def _eligible(detail):
    return (
        not detail.is_article_ng and detail.status != 'rejected' and detail.deleted_at is None
        and detail.slide_url == detail.slide_fetch_url and is_importable(detail.slide_fetch_url)
    )


def _claim_next(now, excluded):
    due = Q(slide_fetch_status=EventDetail.SlideFetchStatus.PENDING) | Q(
        slide_fetch_status=EventDetail.SlideFetchStatus.FETCHING,
        slide_fetch_started_at__lte=now - CLAIM_LEASE,
    )
    candidates = EventDetail.all_objects.filter(due).exclude(pk__in=excluded).order_by('pk').values(
        'pk', 'slide_fetch_status', 'slide_fetch_started_at', 'slide_fetch_url',
    )[:10]
    for candidate in candidates:
        if EventDetail.all_objects.filter(**candidate).update(
            slide_fetch_status=EventDetail.SlideFetchStatus.FETCHING,
            slide_fetch_started_at=now, slide_fetch_attempts=F('slide_fetch_attempts') + 1,
        ):
            return candidate['pk'], now
    return None


def _log_result(detail, outcome, reason=''):
    result = {
        'event_detail_id': detail.pk, 'service': slide_service(detail.slide_fetch_url) or 'unknown',
        'outcome': outcome, 'reason': reason,
    }
    logger.info('slide_fetch', extra={'event_type': 'slide_fetch', **result})
    return result


def _process_one(pk, claimed_at):
    detail = EventDetail.all_objects.get(pk=pk)
    if detail.slide_fetch_started_at != claimed_at or detail.slide_fetch_status != 'fetching':
        return _log_result(detail, 'skipped')
    # 処理が落ちて期限切れで拾い直した行は、回数を超えたら読みに行かずに止める。
    if detail.slide_fetch_attempts > MAX_ATTEMPTS:
        return _release_claim(pk, claimed_at)
    if not _eligible(detail):
        with transaction.atomic():
            current = EventDetail.all_objects.select_for_update().get(pk=pk)
            if current.slide_fetch_started_at == claimed_at:
                reset_slide_fetch(current)
                current.save(update_fields=SLIDE_FETCH_FIELDS[1:])
        return _log_result(detail, 'skipped')
    try:
        pdf, pages = fetch_slide_pdf(detail.slide_fetch_url)
        reason = ''
    except SlideFetchError as exc:
        pdf, pages, reason = None, None, exc.reason

    # 読んでいる間の URL 変更・NG・削除・別の claim を保存直前に確認する。
    with transaction.atomic():
        current = EventDetail.all_objects.select_for_update().get(pk=pk)
        if current.slide_fetch_started_at != claimed_at:
            return _log_result(detail, 'skipped')
        if not _eligible(current) or current.slide_fetch_url != detail.slide_fetch_url:
            reset_slide_fetch(current)
            current.save(update_fields=SLIDE_FETCH_FIELDS[1:])
            return _log_result(detail, 'skipped')
        current.slide_fetch_started_at = None
        if reason:
            retry = reason == 'network' and current.slide_fetch_attempts < MAX_ATTEMPTS
            current.slide_fetch_status = 'pending' if retry else 'failed'
            current.slide_fetch_error = reason
            current.save(update_fields=['slide_fetch_status', 'slide_fetch_error', 'slide_fetch_started_at'])
            return _log_result(detail, 'retry' if retry else 'failed', reason)
        current.slide_source_file.save('source.pdf', pdf, save=False)
        current.slide_source_page_count = pages
        current.slide_fetch_status = 'success'
        current.slide_fetch_error = ''
        current.slide_fetched_at = timezone.now()
        current.save(update_fields=SLIDE_FETCH_FIELDS)
        if current.slide_generate_requested:
            EventDetail.all_objects.filter(pk=pk).update(**article_generation_request_values())
    if not current.thumbnail_image:
        try:
            ensure_pdf_thumbnail(current, save=True)
        except Exception:
            # PDF の保存は済んでいる。サムネイルの失敗で読み込みを失敗にしない。
            logger.warning('slide_fetch_thumbnail_failed: event_detail_id=%s', pk)
    return _log_result(detail, 'success')


def _release_claim(pk, claimed_at):
    """想定外の例外で「読み込み中」が残らないように、再試行か失敗へ戻す。"""
    with transaction.atomic():
        current = EventDetail.all_objects.select_for_update().get(pk=pk)
        if current.slide_fetch_status != 'fetching' or current.slide_fetch_started_at != claimed_at:
            return _log_result(current, 'skipped')
        retry = current.slide_fetch_attempts < MAX_ATTEMPTS
        current.slide_fetch_status = 'pending' if retry else 'failed'
        current.slide_fetch_error = 'internal'
        current.slide_fetch_started_at = None
        current.save(update_fields=['slide_fetch_status', 'slide_fetch_error', 'slide_fetch_started_at'])
    return _log_result(current, 'retry' if retry else 'failed', 'internal')


def process_slide_fetch_queue(limit=1):
    results = []
    while len(results) < limit:
        claimed = _claim_next(timezone.now(), [r['event_detail_id'] for r in results])
        if claimed is None:
            break
        try:
            results.append(_process_one(*claimed))
        except Exception as exc:
            # 例外本文には取得先 URL が含まれうるので、種類だけ記録する。
            logger.error('slide_fetch_unexpected_error: event_detail_id=%s type=%s', claimed[0], type(exc).__name__)
            results.append(_release_claim(*claimed))
    return {'processed': len(results), 'results': results}
