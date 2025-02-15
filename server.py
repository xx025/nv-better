if __name__ == "__main__":
    import uvicorn

    uvicorn.run("core.server:app", host="0.0.0.0", port=12999)
