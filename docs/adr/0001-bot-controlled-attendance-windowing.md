# Bot-controlled attendance windowing

The bot accepts an overall requester range of up to twelve rolling calendar months, then partitions it into contiguous inclusive windows of at most 31 days and uses fixed 50-event offsets. This preserves the Attendance CRMT MCP boundary while enabling longer requests without delegating pagination, source ordering, or range enforcement to the model; a later page or window failure discards the aggregate and returns the established safe failure reply.
