# Account and data handling

Customer accounts in this sample application are fictional. A signed-in customer may retrieve only their own account details. Passwords are stored as salted scrypt hashes rather than plain text.

The sample account balance is not connected to a financial institution and has no monetary value. Do not enter real financial credentials, real account information, or sensitive personal data. Customer and loan CSV files are local application data and must not be committed to version control.

The language model receives the customer's messages and tool results needed to answer them. Do not include secrets in chat. Configure an API provider whose data handling meets your requirements before using the application beyond local demonstration.