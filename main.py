"""Energy invoice extraction API server.

To run the API server:
    uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
"""

if __name__ == "__main__":
    import uvicorn
    from src.api.main import app

    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
