import os
import pyodbc
from dotenv import load_dotenv

load_dotenv()

def _pick_driver() -> str:
    explicit = os.getenv("MSSQL_ODBC_DRIVER")
    if explicit:
        return explicit
    try:
        drivers = pyodbc.drivers()
    except Exception:
        drivers = []
    for candidate in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"):
        if candidate in drivers:
            return candidate
    return "ODBC Driver 17 for SQL Server"


CONN_STR = (
    f"DRIVER={{{_pick_driver()}}};"
    f"SERVER={os.getenv('MSSQL_HOST', 'localhost')},{os.getenv('MSSQL_PORT', '1433')};"
    f"DATABASE={os.getenv('MSSQL_DATABASE', 'service_desk_tdbb')};"
    f"UID={os.getenv('MSSQL_USER', 'SA')};PWD={os.getenv('MSSQL_SA_PASSWORD')};"
    "TrustServerCertificate=yes;Encrypt=no;"
)


def get_connection():
    return pyodbc.connect(CONN_STR)


def fetch_tickets(limit: int = None) -> list[dict]:
    sql = """
        SELECT TOP {limit}
            t.Id,
            t.Name,
            t.Description,
            t.Comment,
            s.NameXml.value('(/Language/Ru)[1]', 'nvarchar(500)') AS ServiceName,
            tt.NameXml.value('(/Language/Ru)[1]', 'nvarchar(500)') AS TaskTypeName,
            st.NameXml.value('(/Language/Ru)[1]', 'nvarchar(500)') AS StatusName,
            p.NameXml.value('(/Language/Ru)[1]', 'nvarchar(500)') AS PriorityName
        FROM Task t
        LEFT JOIN Service s ON t.ServiceId = s.Id
        LEFT JOIN TaskType tt ON t.TypeId = tt.Id
        LEFT JOIN Status st ON t.StatusId = st.Id
        LEFT JOIN Priority p ON t.PriorityId = p.Id
        WHERE t.Comment IS NOT NULL AND LEN(t.Comment) > 50
    """.format(limit=limit if limit else 200000)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]


def fetch_task_expenses(limit: int = None) -> list[dict]:
    sql = """
        SELECT TOP {limit}
            te.Id,
            te.TaskId,
            te.Comments,
            t.Name AS TaskName,
            s.NameXml.value('(/Language/Ru)[1]', 'nvarchar(500)') AS ServiceName
        FROM TaskExpenses te
        JOIN Task t ON te.TaskId = t.Id
        LEFT JOIN Service s ON t.ServiceId = s.Id
        WHERE te.Comments IS NOT NULL AND LEN(te.Comments) > 30
        ORDER BY te.TaskId
    """.format(limit=limit if limit else 500000)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]


def fetch_kb_articles() -> list[dict]:
    sql = """
        SELECT
            d.Id,
            d.Name,
            d.Description,
            f.NameXml.value('(/Language/Ru)[1]', 'nvarchar(500)') AS FolderName
        FROM KBDocument d
        LEFT JOIN KBFolder f ON d.ParentId = f.Id
        WHERE d.IsPublished = 1 AND d.Description IS NOT NULL
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
