from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import LovedOne, Quote
from .quote_generation import generate_personal_quotes_for_loved_one


@receiver(post_save, sender=LovedOne)
def create_personal_quotes_for_loved_one(sender, instance: LovedOne, created: bool, **kwargs):
    if Quote.objects.filter(loved_one=instance, quote_type=Quote.QuoteScope.PERSONAL).exists():
        return

    generate_personal_quotes_for_loved_one(instance)