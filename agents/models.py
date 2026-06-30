# from uagents import Model

# # The message sent TO workers
# class TaskRequest(Model):
#     id: str
#     query: str

# # The message received FROM workers
# class WorkerResponse(Model):
#     request_id: str
#     data: str

from uagents import Model

class TaskRequest(Model):
    # Force the schema digest to a fixed value
    class Schema:
        digest = "e8a5dc3799dd511275b5264a30e7fe010337b42440d1ecb6aaed53d48c2bc7f1"

    id: str
    query: str

class WorkerResponse(Model):
    class Schema:
        digest = "f5a7bc3799dd511275b5264a30e7fe010337b42440d1ecb6aaed53d48c2bc7a2"

    request_id: str
    data: str