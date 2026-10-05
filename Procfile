web: python PolygonMigration/manage.py migrate && python PolygonMigration/manage.py collectstatic --noinput && cd PolygonMigration && gunicorn PolygonMigration.wsgi:application --bind 0.0.0.0:$PORT
