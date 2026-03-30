1. 🟢 Easiest — Swagger UI (Browser)
Open:
http://127.0.0.1:9000/docs
Then for each endpoint, click Try it out → fill in the body → Execute.

2. Employee Lookup — POST /employee/lookup
By name:
jsonPOST http://127.0.0.1:9000/employee/lookup

{
  "name": "Rajesh"
}
By ID:
jsonPOST http://127.0.0.1:9000/employee/lookup

{
  "employee_id": 2
}
curl:
bashcurl -X POST http://127.0.0.1:9000/employee/lookup \
  -H "Content-Type: application/json" \
  -d '{"name": "Rajesh"}'
Expected response:
json{
  "id": 1,
  "name": "Rajesh",
  "age": 30,
  "city": "Utrecht"
}

3. Create Ticket — POST /ticket/create
jsonPOST http://127.0.0.1:9000/ticket/create

{
  "employee_name": "Rajesh",
  "issue": "cannot install Python"
}
curl:
bashcurl -X POST http://127.0.0.1:9000/ticket/create \
  -H "Content-Type: application/json" \
  -d '{"employee_name": "Rajesh", "issue": "cannot install Python"}'
Expected response:
json{
  "ticket_id": 1,
  "employee_id": 1,
  "employee_name": "Rajesh",
  "issue": "cannot install Python"
}

4. Using Postman

Open Postman → New Request
Set method to POST
Enter the URL: http://127.0.0.1:9000/ticket/create
Go to Body tab → select raw → choose JSON
Paste the request body → hit Send