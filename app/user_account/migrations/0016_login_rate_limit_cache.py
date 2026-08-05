"""Create the shared cache table used by login rate limiting."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Create the cache table without adding a runtime Django model."""

    dependencies = [
        ('user_account', '0015_backfill_verified_email_addresses'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.CreateModel(
                    name='LoginRateLimitCache',
                    fields=[
                        (
                            'cache_key',
                            models.CharField(
                                max_length=255,
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        ('value', models.TextField()),
                        ('expires', models.DateTimeField(db_index=True)),
                    ],
                    options={
                        'db_table': 'login_rate_limit_cache',
                    },
                ),
            ],
            # DatabaseCache does not need a runtime model. Keeping it out of
            # migration state prevents a later makemigrations from deleting it.
            state_operations=[],
        ),
    ]
