from urllib.parse import urlencode


def get_filtered_url(base_url, current_params, key, value):
    params = current_params.copy()
    if value in params.getlist(key):
        params.getlist(key).remove(value)
    else:
        params.appendlist(key, value)
    return f"{base_url}?{urlencode(params, doseq=True)}"
