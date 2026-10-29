# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""OpenClear's own extensions, registered through the same interface any plugin uses
(plugins/__init__.py), which keeps that interface honest. Factories import lazily, so an
optional dependency (boto3, twilio) is only needed when it's chosen."""


class _Builtin:
    name = "openclear"
    plugin_api = (1, 1)

    @property
    def version(self) -> str:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("openclear")
        except PackageNotFoundError:
            return "source"

    def register(self, registry) -> None:
        # Payment networks: importing each package registers its adapter (networks/registry.py).
        import openclear.networks.ach  # noqa: F401
        import openclear.networks.check  # noqa: F401
        from openclear.networks.registry import get_adapter

        for code in ("check", "ach"):
            registry.add_network(get_adapter(code))

        def tesseract():
            from openclear.ocr.tesseract_provider import TesseractOCRProvider

            return TesseractOCRProvider()

        def textract():
            from openclear.ocr.textract_provider import TextractOCRProvider

            return TextractOCRProvider()

        def azure_document_intelligence():
            from openclear.ocr.azure_di_provider import AzureDocumentIntelligenceOCRProvider

            return AzureDocumentIntelligenceOCRProvider()

        registry.add_ocr_provider("tesseract", tesseract)
        registry.add_ocr_provider("textract", textract)
        registry.add_ocr_provider("azure_document_intelligence", azure_document_intelligence)

        def smtp():
            from openclear.notifications.email.smtp_provider import SmtpEmailProvider

            return SmtpEmailProvider()

        def twilio():
            from openclear.notifications.sms.twilio_provider import TwilioSmsProvider

            return TwilioSmsProvider()

        registry.add_email_provider("smtp", smtp)
        registry.add_sms_provider("twilio", twilio)

        def local_storage():
            from openclear import storage

            return storage._local

        def s3_storage():
            from openclear import storage

            return storage._s3()

        registry.add_storage_backend("local", local_storage)
        registry.add_storage_backend("s3", s3_storage)

        def logistic_regression():
            from openclear.ml.model import LogisticRegressionScoringModel

            return LogisticRegressionScoringModel()

        registry.add_scoring_model("logistic_regression", logistic_regression)

        from openclear.services.bulk_import_service import BUILTIN_SPECS

        for (kind, file_format), spec in BUILTIN_SPECS.items():
            registry.add_import_format(kind, file_format, spec)

        from openclear.networks.ach import returns_file
        from openclear.plugins.api import ConfigField, DecisionLayout
        from openclear.services.decision_feed_service import to_csv

        from openclear.services.decision_feed_service import custom_fields_setting

        registry.add_decision_layout(
            "openclear_csv", DecisionLayout(
                "OpenClear CSV", "text/csv", "csv", lambda feed, settings: to_csv(feed, custom_fields_setting(settings)).encode(),
                # Custom fields at the end, in this order (plans/CUSTOM_FIELDS_PLAN.md CF-D7).
                config_fields=(ConfigField("custom_fields", "Custom fields to add (keys, comma-separated; a customer's as "
                                           "customer.key)", required=False),),
            ),
        )
        registry.add_decision_layout(
            "nacha_returns", DecisionLayout(
                "NACHA return file (ACH returns)", "text/plain", "ach", lambda feed, settings: returns_file.write(feed, settings),
                config_fields=tuple(ConfigField(name, label) for name, label in returns_file.SETTINGS_FIELDS),
            ),
        )


plugin = _Builtin()
