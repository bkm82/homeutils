import urllib.request


def send_ntfy(
    server_url: str, topic: str, title: str, message: str, priority: str = "default"
) -> None:
    request = urllib.request.Request(
        f"{server_url.rstrip('/')}/{topic}",
        data=message.encode(),
        headers={"Title": title, "Priority": priority},
        method="POST",
    )
    urllib.request.urlopen(request, timeout=10)
