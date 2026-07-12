from django.db import migrations


def backfill_vket_event_detail_applicant(apps, schema_editor):
    """Vket由来の発表詳細へ申請者を補完する。"""
    EventDetail = apps.get_model("event", "EventDetail")
    VketPresentation = apps.get_model("vket", "VketPresentation")

    detail_ids = EventDetail.objects.filter(
        applicant__isnull=True,
        vket_presentations__participation__applied_by__isnull=False,
    ).values_list("pk", flat=True).distinct()

    for detail_id in detail_ids.iterator():
        presentation = (
            VketPresentation.objects.filter(
                published_event_detail_id=detail_id,
                participation__applied_by__isnull=False,
            )
            .order_by("order", "pk")
            .first()
        )
        if presentation:
            EventDetail.objects.filter(
                pk=detail_id,
                applicant__isnull=True,
            ).update(applicant_id=presentation.participation.applied_by_id)


class Migration(migrations.Migration):
    dependencies = [
        ("vket", "0009_alter_vketcollaboration_lt_deadline_and_more"),
    ]

    operations = [
        migrations.RunPython(
            backfill_vket_event_detail_applicant,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
