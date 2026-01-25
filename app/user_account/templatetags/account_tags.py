from django import template

register = template.Library()

@register.filter
def get_field_label(form, field_name):
    try:
        return form.fields[field_name].label
    except (KeyError, AttributeError):
        return field_name 
