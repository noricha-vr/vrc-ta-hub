from io import BytesIO
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.files.base import ContentFile
from django.test import TestCase
from PIL import Image

from community.models import Community
from event.forms import EventDetailForm
from event.libs import ensure_event_detail_thumbnail_from_pdf
from event.models import Event, EventDetail


def _image_file(name="thumbnail.jpg"):
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color="blue").save(buffer, format="JPEG")
    return ContentFile(buffer.getvalue(), name=name)


class EventDetailThumbnailFormTest(TestCase):
    def test_form_contains_thumbnail_upload_field(self):
        form = EventDetailForm()

        self.assertIn("thumbnail", form.fields)
        self.assertEqual(form.fields["thumbnail"].widget.attrs["accept"], "image/*")


class EventDetailThumbnailGenerationTest(TestCase):
    def setUp(self):
        community = Community.objects.create(name="Test Community")
        event = Event.objects.create(date=date(2026, 5, 1), community=community)
        self.event_detail = EventDetail.objects.create(
            event=event,
            detail_type="LT",
            status="pending",
            theme="PDF Thumbnail",
            speaker="Speaker",
        )
        self.event_detail.slide_file.save(
            "slides.pdf",
            ContentFile(b"%PDF-1.4\n%%EOF"),
            save=True,
        )

    def test_generate_thumbnail_from_pdf_first_page(self):
        rendered_image = Image.new("RGB", (16, 9), color="red")
        page = MagicMock()
        page.render.return_value.to_pil.return_value = rendered_image
        document = MagicMock()
        document.__len__.return_value = 1
        document.__getitem__.return_value = page

        pdfium = SimpleNamespace(PdfDocument=MagicMock(return_value=document))
        with patch.dict("sys.modules", {"pypdfium2": pdfium}):
            generated = ensure_event_detail_thumbnail_from_pdf(self.event_detail)

        self.assertTrue(generated)
        self.assertTrue(self.event_detail.thumbnail.name.endswith(".jpg"))
        pdfium.PdfDocument.assert_called_once()
        page.close.assert_called_once()
        document.close.assert_called_once()

    def test_existing_thumbnail_is_not_overwritten(self):
        self.event_detail.thumbnail.save("existing.jpg", _image_file(), save=True)
        original_name = self.event_detail.thumbnail.name

        pdfium = SimpleNamespace(PdfDocument=MagicMock())
        with patch.dict("sys.modules", {"pypdfium2": pdfium}):
            generated = ensure_event_detail_thumbnail_from_pdf(self.event_detail)

        self.assertFalse(generated)
        self.assertEqual(self.event_detail.thumbnail.name, original_name)
        pdfium.PdfDocument.assert_not_called()
