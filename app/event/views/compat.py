import sys


def get_views_module():
    """互換維持のため package 本体を返す。"""
    return sys.modules[__package__]


def get_logger():
    return get_views_module().logger


def get_generate_blog():
    return get_views_module().generate_blog


def get_bigquery_state():
    views_module = get_views_module()
    return views_module._bigquery_client, views_module._bigquery_project


def set_bigquery_state(client, project):
    views_module = get_views_module()
    views_module._bigquery_client = client
    views_module._bigquery_project = project
