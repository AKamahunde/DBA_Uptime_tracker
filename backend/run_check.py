import os
import sys
import django
from django.core.management import call_command
from io import StringIO

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dba_tracker.settings')
django.setup()

out = StringIO()
try:
    call_command('check', stdout=out, stderr=out)
    result = out.getvalue()
    with open('django_check_output.txt', 'w') as f:
        f.write("SUCCESS:\n" + result)
except Exception as e:
    import traceback
    with open('django_check_output.txt', 'w') as f:
        f.write("ERROR:\n" + traceback.format_exc())
