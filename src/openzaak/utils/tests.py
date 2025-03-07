# SPDX-License-Identifier: EUPL-1.2
# Copyright (C) 2019 - 2020 Dimpact
import contextlib
import hashlib
import json
import os
import sys
import uuid
from datetime import date

from django.conf import settings
from django.contrib.sites.models import Site
from django.core import serializers
from django.core.cache import caches
from django.db.models import Model
from django.utils import timezone

from djangorestframework_camel_case.util import camelize
from drc_cmis.client_builder import get_cmis_client
from drc_cmis.models import CMISConfig, UrlMapping
from rest_framework.test import APITestCase, APITransactionTestCase
from vng_api_common.authorizations.models import Applicatie, Autorisatie
from vng_api_common.constants import ComponentTypes, VertrouwelijkheidsAanduiding
from vng_api_common.models import JWTSecret
from vng_api_common.tests import reverse
from zds_client.compat import jwt_encode
from zds_client.tests.mocks import MockClient

from openzaak.accounts.models import User


def generate_jwt_auth(
    client_id,
    secret,
    user_id="test_user_id",
    user_representation="Test User",
    **extra_claims,
):
    """
    Generate a JWT suitable for the second version of the AC-based auth.
    """
    now = int(timezone.now().timestamp())
    payload = {
        # standard claims
        "iss": "testsuite",
        "iat": now,
        # custom
        "client_id": client_id,
        "user_id": user_id,
        "user_representation": user_representation,
    }
    payload.update(**extra_claims)
    encoded = jwt_encode(payload, secret, algorithm="HS256")
    return f"Bearer {encoded}"


class JWTAuthMixin:
    """
    Configure the local auth cache.

    Creates the local auth objects for permission checks, as if you're talking
    to a real AC behind the scenes.
    """

    client_id = "testsuite"
    secret = "letmein"

    user_id = "test_user_id"
    user_representation = "Test User"

    scopes = None
    heeft_alle_autorisaties = False
    component = None
    zaaktype = None
    informatieobjecttype = None
    besluittype = None
    max_vertrouwelijkheidaanduiding = VertrouwelijkheidsAanduiding.zeer_geheim
    host_prefix = "http://testserver"

    @classmethod
    def check_for_instance(cls, obj) -> str:
        if isinstance(obj, Model):
            return cls.host_prefix + reverse(obj)
        return obj

    @classmethod
    def setUpTestData(cls):
        if hasattr(super(), "setUpTestData"):
            super().setUpTestData()

        JWTSecret.objects.get_or_create(
            identifier=cls.client_id, defaults={"secret": cls.secret}
        )

        cls.applicatie = Applicatie.objects.create(
            client_ids=[cls.client_id],
            label="for test",
            heeft_alle_autorisaties=cls.heeft_alle_autorisaties,
        )

        if cls.heeft_alle_autorisaties is False:
            zaaktype_url = cls.check_for_instance(cls.zaaktype)
            besluittype_url = cls.check_for_instance(cls.besluittype)
            informatieobjecttype_url = cls.check_for_instance(cls.informatieobjecttype)

            cls.autorisatie = Autorisatie.objects.create(
                applicatie=cls.applicatie,
                component=cls.component or ComponentTypes.zrc,
                scopes=cls.scopes or [],
                zaaktype=zaaktype_url or "",
                informatieobjecttype=informatieobjecttype_url or "",
                besluittype=besluittype_url or "",
                max_vertrouwelijkheidaanduiding=cls.max_vertrouwelijkheidaanduiding,
            )

    def setUp(self):
        super().setUp()

        token = generate_jwt_auth(
            client_id=self.client_id,
            secret=self.secret,
            user_id=self.user_id,
            user_representation=self.user_representation,
        )
        self.client.credentials(HTTP_AUTHORIZATION=token)


class ClearCachesMixin:
    def setUp(self):
        self._clear_caches()
        self.addCleanup(self._clear_caches)

    def _clear_caches(self):
        for cache in caches.all():
            cache.clear()


class AdminTestMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.user = User.objects.create_superuser(
            username="demo",
            email="demo@demo.com",
            password="demo",
            first_name="first",
            last_name="last",
        )

    def setUp(self) -> None:
        super().setUp()
        self.client.login(username="demo", password="demo")

    def tearDown(self) -> None:
        super().tearDown()
        self.client.logout()


no_fetch = object()


@contextlib.contextmanager
def mock_client(responses: dict):
    try:
        from django.test import override_settings
    except ImportError as exc:
        raise ImportError("You can only use this in a django context") from exc

    try:
        json_string = json.dumps(responses).encode("utf-8")
        md5 = hashlib.md5(json_string).hexdigest()
        name = f"MockClient{md5}"
        # create the class
        type(name, (MockClient,), {"responses": responses})
        dotted_path = f"zds_client.tests.mocks.{name}"
        with override_settings(ZGW_CONSUMERS_CLIENT_CLASS=dotted_path):
            yield

        # clean up
        delattr(sys.modules["zds_client.tests.mocks"], name)
    finally:
        pass


class MockSchemasMixin:
    """
    Mock fetching the schema's from Github.
    """

    mocker_attr = "adapter"

    def setUp(self):
        from openzaak.tests.utils import mock_service_oas_get

        super().setUp()

        mocker = getattr(self, self.mocker_attr)

        mock_service_oas_get(mocker, "brc", oas_url=settings.BRC_API_SPEC)
        mock_service_oas_get(mocker, "drc", oas_url=settings.DRC_API_SPEC)
        mock_service_oas_get(mocker, "zrc", oas_url=settings.ZRC_API_SPEC)
        mock_service_oas_get(mocker, "ztc", oas_url=settings.ZTC_API_SPEC)


class CMISMixin(MockSchemasMixin):
    @classmethod
    def setUpTestData(cls):
        if hasattr(super(), "setUpTestData"):
            super().setUpTestData()

        site = Site.objects.get_current()
        site.domain = "testserver"
        site.save()

        binding = os.getenv("CMIS_BINDING")
        if binding == "WEBSERVICE":
            config = CMISConfig.get_solo()
            config.client_url = "http://localhost:8082/alfresco/cmisws"
            config.binding = "WEBSERVICE"
            config.other_folder_path = "/DRC/"
            config.zaak_folder_path = "/ZRC/{{ zaaktype }}/{{ zaak }}"
            config.save()

            # Configure the main_repo_id
            client = get_cmis_client()
            config.main_repo_id = client.get_main_repo_id()
            config.save()
        elif binding == "BROWSER":
            config = CMISConfig.get_solo()
            config.client_url = "http://localhost:8082/alfresco/api/-default-/public/cmis/versions/1.1/browser"
            config.binding = "BROWSER"
            config.other_folder_path = "/DRC/"
            config.zaak_folder_path = "/ZRC/{{ zaaktype }}/{{ zaak }}/"
            config.save()
        else:
            raise Exception("No CMIS binding specified")

        if settings.CMIS_URL_MAPPING_ENABLED:
            UrlMapping.objects.create(
                long_pattern="http://testserver",
                short_pattern="http://ts",
                config=config,
            )
            UrlMapping.objects.create(
                long_pattern="http://example.com",
                short_pattern="http://ex.com",
                config=config,
            )
            UrlMapping.objects.create(
                long_pattern="http://openzaak.nl",
                short_pattern="http://oz.nl",
                config=config,
            )

    def setUp(self) -> None:
        import requests_mock

        # real_http=True to let the other calls pass through and use a different mocker
        # in specific tests cases to catch those requests
        self.adapter = requests_mock.Mocker(real_http=True)
        self.adapter.start()

        self.addCleanup(self._cleanup_alfresco)

        # testserver vs. example.com
        Site.objects.clear_cache()

        super().setUp()

    def _cleanup_alfresco(self) -> None:
        # Removes the created documents from alfresco
        client = get_cmis_client()
        client.delete_cmis_folders_in_base()
        self.adapter.stop()


class APICMISTestCase(CMISMixin, APITestCase):
    pass


class APICMISTransactionTestCase(CMISMixin, APITransactionTestCase):
    pass


def get_eio_response(url, **overrides):
    eio_type = (
        f"https://external.catalogus.nl/api/v1/informatieobjecttypen/{uuid.uuid4()}"
    )
    eio = {
        "url": url,
        "identificatie": "DOCUMENT-00001",
        "bronorganisatie": "272618196",
        "creatiedatum": date.today().isoformat(),
        "titel": "some titel",
        "auteur": "some auteur",
        "status": "",
        "formaat": "some formaat",
        "taal": "nld",
        "beginRegistratie": timezone.now().isoformat().replace("+00:00", "Z"),
        "versie": 1,
        "bestandsnaam": "",
        "inhoud": f"{url}/download?versie=1",
        "bestandsomvang": 100,
        "link": "",
        "beschrijving": "",
        "ontvangstdatum": None,
        "verzenddatum": None,
        "ondertekening": {"soort": "", "datum": None},
        "indicatieGebruiksrecht": None,
        "vertrouwelijkheidaanduiding": "openbaar",
        "integriteit": {"algoritme": "", "waarde": "", "datum": None},
        "informatieobjecttype": eio_type,
        "locked": False,
    }
    eio.update(**overrides)

    if overrides.get("_informatieobjecttype_url") is not None:
        eio["informatieobjecttype"] = overrides.get("_informatieobjecttype_url")

    return eio


def serialise_eio(eio, eio_url, **overrides):
    serialised_eio = json.loads(serializers.serialize("json", [eio,]))[0]["fields"]
    serialised_eio = get_eio_response(eio_url, **serialised_eio, **overrides)
    return camelize(serialised_eio)
