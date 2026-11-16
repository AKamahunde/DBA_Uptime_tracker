from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0025_create_system_administration_group'),
    ]

    operations = [
        migrations.AddField(
            model_name='serverhealth',
            name='max_connections',
            field=models.IntegerField(default=0, help_text='Configured max_connections limit'),
        ),
    ]
