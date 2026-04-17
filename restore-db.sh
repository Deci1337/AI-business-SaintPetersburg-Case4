#!/usr/bin/env bash
# Восстанавливает базу данных из backup при первом запуске
set -euo pipefail

BACKUP_PATH="/var/opt/mssql/backup/cleaned.bak"
DB_NAME="service_desk_tdbb"
SA_PASSWORD="${MSSQL_SA_PASSWORD:?}"
SQLCMD="/opt/mssql-tools18/bin/sqlcmd"

echo "Waiting for SQL Server to start..."
for i in $(seq 1 60); do
    if $SQLCMD -S localhost -U SA -P "$SA_PASSWORD" -Q "SELECT 1" -b -o /dev/null 2>/dev/null; then
        echo "SQL Server is ready"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "ERROR: SQL Server did not start in time"
        exit 1
    fi
    sleep 2
done

# Check if DB already exists
EXISTS=$($SQLCMD -S localhost -U SA -P "$SA_PASSWORD" -Q "SET NOCOUNT ON; SELECT COUNT(*) FROM sys.databases WHERE name='$DB_NAME'" -h -1 -b 2>/dev/null | tr -d '[:space:]')

if [ "$EXISTS" = "0" ]; then
    echo "Restoring $DB_NAME from backup..."
    $SQLCMD -S localhost -U SA -P "$SA_PASSWORD" -Q "
        RESTORE DATABASE [$DB_NAME]
        FROM DISK = '$BACKUP_PATH'
        WITH MOVE 'service_desk_tdbb'     TO '/var/opt/mssql/data/${DB_NAME}.mdf',
             MOVE 'service_desk_tdbb_log' TO '/var/opt/mssql/data/${DB_NAME}_log.ldf',
             REPLACE
    " -b
    echo "Database restored successfully"
else
    echo "Database $DB_NAME already exists, skipping restore"
fi
