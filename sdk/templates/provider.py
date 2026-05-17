class ExampleProvider:
    def search(self, video, languages, config):
        if not config.get("api_token"):
            raise ValueError("api_token is required")
        return []

    def download(self, provider_payload, language, config):
        if not config.get("api_token"):
            raise ValueError("api_token is required")
        return None
