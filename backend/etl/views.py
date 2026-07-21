import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from api.models import ReplicationMonitor

@csrf_exempt
def ingest_replication_status(request):
    """
    Endpoint for receiving MySQL replication status updates via POST.
    Expected JSON payload:
    {
        "master": "192.168.35.46",
        "replica": "nickeldbdr",
        "io_thread": "Yes",
        "sql_thread": "Yes",
        "seconds_behind": 0,
        "last_error": ""
    }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST method is allowed'}, status=405)

    try:
        data = json.loads(request.body)
        
        # Extract fields
        master = data.get('master')
        replica = data.get('replica')
        io_thread = data.get('io_thread', 'No')
        sql_thread = data.get('sql_thread', 'No')
        seconds_behind = data.get('seconds_behind', 0)
        last_error = data.get('last_error', '')

        if not master or not replica:
            return JsonResponse({'error': 'Missing required fields: master, replica'}, status=400)

        # Calculate severity based on health
        severity = 'Low'
        if io_thread != 'Yes' or sql_thread != 'Yes':
            severity = 'Critical'
        elif isinstance(seconds_behind, int) and seconds_behind > 300:
            severity = 'High'
        elif isinstance(seconds_behind, int) and seconds_behind > 60:
            severity = 'Medium'

        # Create record
        record = ReplicationMonitor.objects.create(
            date=timezone.now().date(),
            master=master,
            replica=replica,
            io_thread=io_thread,
            sql_thread=sql_thread,
            seconds_behind=seconds_behind if seconds_behind != '' else None,
            last_error=last_error,
            database='MySQL',
            severity=severity,
            created_by=None  # System generated
        )

        # The ReplicationMonitor model has a custom save() method that will calculate the status (Healthy/Failed)
        
        return JsonResponse({'success': True, 'record_id': record.id, 'message': 'Replication status ingested successfully'}, status=201)

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON payload'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
