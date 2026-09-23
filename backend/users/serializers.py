try:
    from rest_framework import serializers
    DRF_AVAILABLE = True
except ImportError:
    DRF_AVAILABLE = False
    serializers = None

from django.contrib.auth.password_validation import validate_password
from .models import User

if DRF_AVAILABLE:
    class UserSerializer(serializers.ModelSerializer):
        class Meta:
            model = User
            fields = ['id', 'username', 'email', 'is_admin', 'must_change_password', 'is_staff', 'is_active', 'created_at']
            read_only_fields = ['id', 'created_at']
else:
    class UserSerializer:
        pass

class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'password_confirm']

    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({"password": "Password fields didn't match."})
        return attrs

    def create(self, validated_data):
        validated_data.pop('password_confirm')
        user = User.objects.create_user(**validated_data)
        return user
