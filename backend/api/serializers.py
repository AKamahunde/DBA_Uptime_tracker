from rest_framework import serializers
from .models import IncidentRegister, ReplicationMonitor, BackupRecovery, ServerHealth, MaintenanceRegister, CapacityPlanning

class IncidentRegisterSerializer(serializers.ModelSerializer):
    class Meta:
        model = IncidentRegister
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)

class ReplicationMonitorSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReplicationMonitor
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)

class BackupRecoverySerializer(serializers.ModelSerializer):
    class Meta:
        model = BackupRecovery
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)

class ServerHealthSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServerHealth
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)

class MaintenanceRegisterSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaintenanceRegister
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)

class CapacityPlanningSerializer(serializers.ModelSerializer):
    class Meta:
        model = CapacityPlanning
        fields = '__all__'
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']

    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)
