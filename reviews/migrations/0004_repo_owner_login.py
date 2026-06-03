from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reviews', '0003_comment_fixed_code_after_comment_fixed_code_before'),
    ]

    operations = [
        migrations.AddField(
            model_name='repo',
            name='owner_login',
            field=models.CharField(blank=True, db_index=True, max_length=100),
        ),
    ]
