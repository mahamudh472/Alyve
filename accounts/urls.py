from django.urls import path
from .views import RevenueCatWebhookView

urlpatterns = [
    path('webhooks/revenuecat/', RevenueCatWebhookView.as_view(), name='revenuecat-webhook'),
]

