from django.core.management.base import BaseCommand
from accounts.models import Plan


class Command(BaseCommand):
    help = 'Seeds the database with the default subscription plans: Basic, Popular, and Premium.'

    def handle(self, *args, **options):
        plans_data = [
            {
                "name": "Basic",
                "price": 29.00,
                "clone_limit": 1,
                "talk_time_limit": 3600,  # 1 hour (3600 seconds)
                "description": "Basic subscription plan with 1 voice clone and 1 hour of talk time.",
                "revenuecat_product_id": "monthly_basic"
            },
            {
                "name": "Popular",
                "price": 45.00,
                "clone_limit": 3,
                "talk_time_limit": 18000,  # 5 hours (18000 seconds)
                "description": "Popular subscription plan with 3 voice clones and 5 hours of talk time.",
                "revenuecat_product_id": "monthly_popular"
            },
            {
                "name": "Premium",
                "price": 75.00,
                "clone_limit": 5,
                "talk_time_limit": 36000,  # 10 hours (36000 seconds)
                "description": "Premium subscription plan with 5 voice clones and 10 hours of talk time.",
                "revenuecat_product_id": "monthly_premium"
            }
        ]

        for data in plans_data:
            plan, created = Plan.objects.update_or_create(
                name=data["name"],
                defaults={
                    "price": data["price"],
                    "clone_limit": data["clone_limit"],
                    "talk_time_limit": data["talk_time_limit"],
                    "description": data["description"],
                    "revenuecat_product_id": data["revenuecat_product_id"],
                    "is_active": True
                }
            )
            action = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{action} plan '{plan.name}' (Price: ${plan.price}, Clones: {plan.clone_limit}, Talk Time: {plan.talk_time_limit}s)"))
