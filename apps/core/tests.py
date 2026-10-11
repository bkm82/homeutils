import pytest
from django.test import Client
from django.urls import reverse


def test_home_page_requires_login(client: Client) -> None:
    response = client.get(reverse("core:home"))
    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.django_db
def test_home_page_loads_when_authenticated(client: Client, django_user_model) -> None:
    user = django_user_model.objects.create_user(username="alice", password="pw")
    client.force_login(user)
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
