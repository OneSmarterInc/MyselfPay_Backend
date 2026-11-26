import os
from pathlib import Path
from urllib.parse import urlsplit,unquote
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from django.core.management.base import BaseCommand,CommandError
from chr_wisconsin import validate_source,source_rows

class Command(BaseCommand):
    help='Atomically replace the dedicated Wisconsin CHR source table; other CHR tables are unchanged.'
    def add_arguments(self,parser):
        parser.add_argument('csv_file');parser.add_argument('--dry-run',action='store_true')
    def handle(self,*args,**options):
        path=Path(options['csv_file'])
        try:count,hospitals,digest=validate_source(path)
        except (ValueError,OSError) as error:raise CommandError(str(error)) from None
        self.stdout.write(f'{count:,} rows; {hospitals} Wisconsin hospitals. Duplicate source rows and missing values are preserved.')
        if options['dry_run']:return
        import chr_index
        runtime=chr_index.database_url()
        target=os.getenv('CHR_IMPORT_DATABASE_URL') or runtime
        if urlsplit(target).scheme not in ('postgres','postgresql') or urlsplit(target).path.lstrip('/')!='selfpay_hospital_compare_v10':raise CommandError('Import must target the configured CHR database selfpay_hospital_compare_v10.')
        try:
            with psycopg.connect(target,connect_timeout=10) as db:
                db.execute('SELECT pg_advisory_xact_lock(74622026)')
                db.execute((Path(__file__).resolve().parents[3]/'ops'/'chr-wisconsin.sql').read_text())
                db.execute('DELETE FROM public.wi_hospital_imaging_prices')
                with db.cursor().copy('COPY public.wi_hospital_imaging_prices(source_row,state,hospital,distance_miles,cash_price,gross_charge,insurer_rates,raw_values,source_sha256) FROM STDIN') as copy:
                    imported=0
                    for row in source_rows(path):copy.write_row((*row[:7],Jsonb(row[7]),digest));imported+=1
                if imported!=count or validate_source(path)[2]!=digest:raise ValueError('CSV changed during import; transaction rolled back.')
                reader=unquote(urlsplit(runtime).username or '')
                if reader:db.execute(sql.SQL('GRANT SELECT ON public.wi_hospital_imaging_prices TO {}').format(sql.Identifier(reader)))
                db.execute('ANALYZE public.wi_hospital_imaging_prices')
        except (psycopg.Error,ValueError):raise CommandError('Import failed and was rolled back. Verify CHR_IMPORT_DATABASE_URL has CREATE/INSERT/DELETE/GRANT permissions and the CSV is unchanged. Credentials are not logged.') from None
        self.stdout.write(self.style.SUCCESS(f'Imported {count:,} rows into public.wi_hospital_imaging_prices.'))
