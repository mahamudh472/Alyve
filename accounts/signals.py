from .models import SiteSetting
from django.db.models.signals import post_migrate
from django.dispatch import receiver

@receiver(post_migrate)
def create_site_setting(sender, **kwargs):
    if sender.name == 'accounts':  # Ensure this runs only for the accounts app
        if not SiteSetting.objects.exists():
            SiteSetting.objects.create()  # Create a default SiteSetting instance
