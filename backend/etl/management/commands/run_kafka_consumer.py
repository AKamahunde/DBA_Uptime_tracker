from django.core.management.base import BaseCommand
from etl.services.kafka_consumer import start_consumer

class Command(BaseCommand):
    help = 'Starts the Kafka consumer to listen for ETL messages'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting Kafka ETL consumer...'))
        try:
            start_consumer()
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('Kafka consumer stopped by user.'))
