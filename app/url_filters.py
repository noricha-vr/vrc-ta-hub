from urllib.parse import urlencode


def get_filtered_url(base_url, current_params, key, value):
    params = current_params.copy()
    if value in params.getlist(key):
        values = params.getlist(key)
        values.remove(value)
        params.setlist(key, values)
    else:
        params.appendlist(key, value)
    encoded_params = urlencode(params, doseq=True)
    if not encoded_params:
        return base_url
    return f"{base_url}?{encoded_params}"
