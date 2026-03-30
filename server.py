from fastmcp import FastMCP
import pandas as pd
import os

# ── Load employee data ─────────────────────────────────────────
EMPLOYEES_CSV = "data.csv"
TICKETS_CSV   = "tickets.csv"

df_employees = pd.read_csv(EMPLOYEES_CSV)
print("Employees loaded:")
print(df_employees)

# ── Create server ──────────────────────────────────────────────
app = FastMCP("ticket-server")


# ─────────────────────────────────────────────────────────────
# TOOL 1 — Look up an employee by name or id
# ─────────────────────────────────────────────────────────────
@app.tool("get_employee")
def get_employee(name: str = "", employee_id: int = 0):
    """
    Returns employee details by name (case-insensitive) or by id.
    Provide at least one of: name or employee_id.
    """
    if name:
        row = df_employees[df_employees["name"].str.lower() == name.lower()]
    elif employee_id:
        row = df_employees[df_employees["id"] == employee_id]
    else:
        return {"error": "Provide at least one of: name or employee_id."}

    if row.empty:
        return {"error": f"No employee found for name='{name}' / id={employee_id}"}
    return row.to_dict(orient="records")[0]


# ─────────────────────────────────────────────────────────────
# TOOL 2 — Create / append a support ticket
# ─────────────────────────────────────────────────────────────
@app.tool("create_ticket")
def create_ticket(employee_name: str, issue: str):
    """
    Creates a support ticket for an employee.
    Looks up the employee by name, assigns the next ticket_id,
    and appends the record to tickets.csv.

    Args:
        employee_name: Full name of the employee (case-insensitive).
        issue: Short description of the issue.

    Returns:
        The newly created ticket record, or an error dict.
    """
    emp_row = df_employees[df_employees["name"].str.lower() == employee_name.lower()]
    if emp_row.empty:
        return {"error": f"Employee '{employee_name}' not found in data.csv"}

    emp      = emp_row.iloc[0]
    emp_id   = int(emp["id"])
    emp_name = emp["name"]

    if os.path.exists(TICKETS_CSV):
        df_tickets     = pd.read_csv(TICKETS_CSV)
        next_ticket_id = int(df_tickets["ticket_id"].max()) + 1
    else:
        df_tickets     = pd.DataFrame(columns=["ticket_id", "employee_id", "employee_name", "issue"])
        next_ticket_id = 1

    new_record = {
        "ticket_id":     next_ticket_id,
        "employee_id":   emp_id,
        "employee_name": emp_name,
        "issue":         issue,
    }

    df_tickets = pd.concat([df_tickets, pd.DataFrame([new_record])], ignore_index=True)
    df_tickets.to_csv(TICKETS_CSV, index=False)

    print(f"✅ Ticket #{next_ticket_id} created → {new_record}")
    return new_record


# ─────────────────────────────────────────────────────────────
# TOOL 3 — List all tickets
# ─────────────────────────────────────────────────────────────
@app.tool("list_tickets")
def list_tickets():
    """
    Returns all tickets recorded in tickets.csv.
    """
    if not os.path.exists(TICKETS_CSV):
        return {"message": "No tickets found. tickets.csv does not exist yet."}
    df_tickets = pd.read_csv(TICKETS_CSV)
    return df_tickets.to_dict(orient="records")


# ── Run with SSE transport so client.py can connect ───────────
if __name__ == "__main__":
    app.run(transport="sse", host="127.0.0.1", port=8000)
