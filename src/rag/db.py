import os
import pyodbc
from dotenv import load_dotenv

load_dotenv()

CONN_STR = (
    "DRIVER={ODBC Driver 18 for SQL Server};"
    "SERVER=localhost,1433;"
    f"DATABASE={os.getenv('MSSQL_DB', 'service_desk_tdbb')};"
    f"UID=SA;PWD={os.getenv('MSSQL_SA_PASSWORD')};"
    "TrustServerCertificate=yes;"
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
            s.Name AS ServiceName,
            tt.Name AS TaskTypeName,
            st.Name AS StatusName,
            p.Name AS PriorityName
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


def fetch_kb_articles() -> list[dict]:
    sql = """
        SELECT
            d.Id,
            d.Name,
            d.Description,
            f.Name AS FolderName
        FROM KBDocument d
        LEFT JOIN KBFolder f ON d.FolderId = f.Id
        WHERE d.IsPublished = 1 AND d.Description IS NOT NULL
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
