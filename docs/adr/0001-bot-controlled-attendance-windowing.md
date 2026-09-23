# Bot-controlled attendance windowing

Each model-selected requester range is validated against Attendance CRMT's
inclusive 31-day contract. The bounded agent loop may issue at most three
sequential calls, always with bot-owned `limit=50` and `offset=0`; it does not
delegate pagination, employee targeting, or authority to the model. Any MCP
failure discards every accumulated projection and returns a safe reply.
