from django.test import Client
from django.urls import reverse


def test_home_page_loads(client: Client) -> None:
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
