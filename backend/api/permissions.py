from rest_framework import permissions

class IsAdminOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow admins to edit records.
    Regular users can only create new records but not edit existing ones.
    """
    
    def has_permission(self, request, view):
        # Authenticated users can always view and create
        return request.user.is_authenticated
    
    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed to any authenticated user
        if request.method in permissions.SAFE_METHODS:
            return True
        
        # Write permissions are only allowed to admins
        return request.user.is_admin

class IsAdminOrCreatorReadOnly(permissions.BasePermission):
    """
    Custom permission to allow:
    - Admins: Full CRUD access
    - Regular users: Can create, but can only edit their own unsaved records
    """
    
    def has_permission(self, request, view):
        return request.user.is_authenticated
    
    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed to any authenticated user
        if request.method in permissions.SAFE_METHODS:
            return True
        
        # Admins have full write access
        if request.user.is_admin:
            return True
        
        # Regular users can only edit if they created it and it's recent (within 5 minutes)
        if obj.created_by == request.user:
            from django.utils import timezone
            from datetime import timedelta
            time_diff = timezone.now() - obj.created_at
            if time_diff.total_seconds() < 300:  # 5 minutes
                return True
        
        return False
