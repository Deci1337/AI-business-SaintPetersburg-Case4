#!/bin/bash
# Восстановление БД из бэкапа внутри контейнера
/opt/mssql-tools18/bin/sqlcmd \
  -S localhost -U SA -P "$MSSQL_SA_PASSWORD" -No \
  -Q "RESTORE DATABASE [service_desk_tdbb] FROM DISK='/var/opt/mssql/backup/cleaned.bak' WITH MOVE 'service_desk_tdbb' TO '/var/opt/mssql/data/service_desk_tdbb.mdf', MOVE 'service_desk_tdbb_log' TO '/var/opt/mssql/data/service_desk_tdbb_log.ldf', REPLACE"
