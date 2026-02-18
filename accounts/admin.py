from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display
from .models import User, SiteSetting
from .forms import SiteSettingForm


@admin.register(User)
class UserAdmin(ModelAdmin):
    list_display = ('email', 'full_name', 'display_status', 'is_staff')
    search_fields = ('email', 'full_name')
    list_filter = ('is_active', 'is_staff')
    ordering = ('email',)
    list_filter_submit = True
    
    @display(description="Status", label={"Active": "success", "Inactive": "danger"})
    def display_status(self, obj):
        return "Active" if obj.is_active else "Inactive"


@admin.register(SiteSetting)
class SiteSettingAdmin(ModelAdmin):
    form = SiteSettingForm
    
    fieldsets = (
        ('Privacy Policy', {
            'fields': ('privacy_policy',),
            'description': 'Add content blocks for the Privacy Policy page. Each block can have a title, description, list items, and footer text.',
        }),
        ('Terms of Service', {
            'fields': ('terms_of_service',),
            'description': 'Add content blocks for the Terms of Service page. Each block can have a title, description, list items, and footer text.',
        }),
        ('Contact Information', {
            'fields': ('support_email',),
        }),
    )

    def has_add_permission(self, request):
        # Prevent adding new SiteSetting instances
        return not SiteSetting.objects.exists()  # Allow add only if no instance exists

    def has_delete_permission(self, request, obj=None):
        # Prevent deletion of SiteSetting instances
        return False  # Disallow delete

    def changelist_view(self, request, extra_context=None):
        # Redirect to the change view of the existing SiteSetting instance
        try:
            site_setting = SiteSetting.objects.get()
            return self.change_view(request, object_id=str(site_setting.id))
        except SiteSetting.DoesNotExist:
            return super().changelist_view(request, extra_context)
