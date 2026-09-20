"""Supervised browser connector for Karnataka iPGRS grievances.

The connector intentionally stops before the resident-controlled OTP/CAPTCHA
boundary.  A run is created only after the local preparation has been reviewed
and approved; the browser then fills the approved values and keeps the portal
open for the resident.  A final submit is recorded only when the portal
returns a grievance number that we can show in the receipt.

Playwright is an optional dependency.  Keeping its import inside ``start``
means the rest of CivitasX remains usable when the browser extra is not
installed or when a deployment has disabled live filing.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import get_settings
from .models import AgentRun, AgentRunStatus, TicketPreparation, TicketStatus
from .policy import PolicyEngine

IPGRS_CONNECTOR_ID = "karnataka-ipgrs-grievances"


def validate_ipgrs_fields(fields: dict[str, str]) -> list[str]:
    """Return user-facing validation errors before opening the official form."""

    errors: list[str] = []
    description = fields.get("description", "")
    if len(re.findall(r"\b[\w'-]+\b", description)) < 25:
        errors.append("description must contain at least 25 words for the iPGRS form")
    if fields.get("pincode") and not re.fullmatch(r"\d{6}", fields["pincode"].strip()):
        errors.append("pincode must contain six digits")
    if fields.get("mobile") and not re.fullmatch(r"[6-9]\d{9}", fields["mobile"].strip()):
        errors.append("mobile must be a valid ten-digit Indian number")
    return errors


class IpgsConnectorError(RuntimeError):
    """A recoverable portal or browser-connector failure."""


@dataclass
class _BrowserSession:
    owner_id: str
    run_id: str
    ticket_id: str
    content_hash: str
    page: Any
    browser: Any
    playwright: Any
    approved_fields: dict[str, str] = field(default_factory=dict)
    filled_fields: list[str] = field(default_factory=list)
    otp_requested: bool = False
    mobile_verified: bool = False


class IpgsBrowserManager:
    """Own open Playwright sessions for supervised filing runs."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], _BrowserSession] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _community_update(
        community: Any,
        owner_id: str,
        run_id: str,
        **kwargs: Any,
    ) -> AgentRun:
        return community.update_run(owner_id, run_id, **kwargs)

    @staticmethod
    async def _close_session(session: _BrowserSession) -> None:
        for resource, method in ((session.browser, "close"), (session.playwright, "stop")):
            if resource is None:
                continue
            try:
                await getattr(resource, method)()
            except Exception:
                # A crashed browser should never prevent the run receipt from
                # being written or a later run from starting.
                continue

    @staticmethod
    async def _locator(page: Any, selectors: list[str]) -> Any | None:
        for selector in selectors:
            locator = page.locator(selector)
            try:
                if await locator.count():
                    return locator.first
            except Exception:
                continue
        return None

    @classmethod
    async def _fill(cls, page: Any, selectors: list[str], value: str, label: str) -> None:
        locator = await cls._locator(page, selectors)
        if locator is None:
            raise IpgsConnectorError(f"The iPGRS page did not expose the {label} field")
        try:
            await locator.fill(value)
        except Exception as exc:
            raise IpgsConnectorError(f"The iPGRS {label} field is not ready") from exc

    @classmethod
    async def _select(cls, page: Any, selectors: list[str], value: str, label: str) -> None:
        locator = await cls._locator(page, selectors)
        if locator is None:
            raise IpgsConnectorError(f"The iPGRS page did not expose the {label} selector")
        value = value.strip()
        try:
            await locator.select_option(value=value)
            return
        except Exception:
            pass
        try:
            await locator.select_option(label=value)
            return
        except Exception:
            pass

        # Government portals often use a numeric option value while residents
        # supply the visible name.  Resolve an exact or case-insensitive label
        # without guessing a partial option.
        options = await locator.locator("option").all()
        for option in options:
            option_label = (await option.inner_text()).strip()
            option_value = (await option.get_attribute("value")) or ""
            if option_label.casefold() == value.casefold():
                await locator.select_option(value=option_value)
                return
        available = [
            (await option.inner_text()).strip()
            for option in options
            if (await option.inner_text()).strip()
        ][:12]
        suffix = f" Available options: {', '.join(available)}" if available else ""
        raise IpgsConnectorError(f"The iPGRS {label} option was not found for {value!r}.{suffix}")

    @staticmethod
    async def _set_files(page: Any, paths: list[Path]) -> bool:
        locator = await IpgsBrowserManager._locator(page, ["#fileUpload", "input[type=file]"])
        if locator is None or not paths:
            return False
        await locator.set_input_files([str(path) for path in paths])
        return True

    @classmethod
    async def _click(cls, page: Any, selectors: list[str], label: str) -> None:
        locator = await cls._locator(page, selectors)
        if locator is None:
            raise IpgsConnectorError(f"The iPGRS {label} control is not available")
        try:
            if not await locator.is_visible():
                raise IpgsConnectorError(
                    f"Advance to the iPGRS {label} step in the open browser first"
                )
            await locator.click()
        except IpgsConnectorError:
            raise
        except Exception as exc:
            raise IpgsConnectorError(f"The iPGRS {label} control could not be clicked") from exc

    @staticmethod
    async def _wait_for_portal(page: Any, url: str) -> None:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            # The portal may keep analytics or status requests open. The form
            # itself is usable once DOMContentLoaded has completed.
            pass

    @staticmethod
    async def _detect_mobile_verified(page: Any) -> bool:
        selectors = [
            "#mobileVerifiedBadge",
            ".mobile-verified",
            "[data-mobile-verified='true']",
        ]
        for selector in selectors:
            locator = page.locator(selector)
            try:
                if await locator.count() and await locator.first.is_visible():
                    return True
            except Exception:
                continue
        try:
            text = (await page.locator("body").inner_text()).casefold()
        except Exception:
            return False
        return bool(re.search(r"mobile\s*(?:number\s*)?(?:is\s*)?verified|otp\s*verified", text))

    @classmethod
    async def _approved_text_field_mismatches(
        cls, page: Any, approved_fields: dict[str, str]
    ) -> list[str]:
        """Ensure resident edits did not change the hash-bound core payload."""

        selectors = {
            "description": ["#grevience_description", "#grievance_description"],
            "address": ["#Address", "#address"],
            "pincode": ["#pincode", "#Pincode"],
            "mobile": ["#UserMobileNumber", "#MobileNumber", "#mobile"],
        }
        mismatches: list[str] = []
        for field_name, field_selectors in selectors.items():
            expected = str(approved_fields.get(field_name) or "").strip()
            if not expected:
                continue
            locator = await cls._locator(page, field_selectors)
            if locator is None:
                mismatches.append(field_name)
                continue
            try:
                actual = str(await locator.input_value()).strip()
            except Exception:
                mismatches.append(field_name)
                continue
            if field_name == "mobile":
                actual = re.sub(r"\D", "", actual)
                expected = re.sub(r"\D", "", expected)
            elif field_name in {"description", "address"}:
                actual = " ".join(actual.split())
                expected = " ".join(expected.split())
            if actual != expected:
                mismatches.append(field_name)
        return mismatches

    @staticmethod
    def _extract_grievance_id(text: str) -> str | None:
        patterns = (
            r"(?:grievance|grevience|complaint)\s*(?:id|no\.?|number)\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9/_-]{3,})",
            r"(?:acknowledg(?:e)?ment|reference)\s*(?:id|no\.?|number)\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9/_-]{3,})",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip(".,;:)")
        return None

    async def start(
        self,
        *,
        owner_id: str,
        run_id: str,
        ticket_id: str,
        preparation: TicketPreparation,
        community: Any,
        settings: Any,
    ) -> AgentRun:
        """Open the official form and fill the approved preparation."""

        validation_errors = validate_ipgrs_fields(preparation.fields)
        if validation_errors:
            return self._community_update(
                community,
                owner_id,
                run_id,
                status=AgentRunStatus.FAILED,
                message="; ".join(validation_errors),
                connector_id=IPGRS_CONNECTOR_ID,
            )

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return self._community_update(
                community,
                owner_id,
                run_id,
                status=AgentRunStatus.FAILED,
                message=(
                    "Live filing needs the optional Playwright browser dependency. "
                    "Install the API browser extra, then retry this approved preparation."
                ),
                connector_id=IPGRS_CONNECTOR_ID,
            )

        key = (owner_id, run_id)
        playwright = None
        browser = None
        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(
                headless=settings.ipgrs_browser_headless,
            )
            context = await browser.new_context()
            page = await context.new_page()
            page.set_default_timeout(15_000)
            await self._wait_for_portal(page, settings.ipgrs_browser_url)

            fields = preparation.fields
            filled: list[str] = []
            manual_fields: list[str] = []
            await self._fill(
                page,
                ["#grevience_description", "#grievance_description"],
                fields["description"],
                "description",
            )
            filled.append("description")
            await self._select(page, ["#District", "#district"], fields["district"], "district")
            filled.append("district")
            # Taluk options are populated after district selection.
            await page.wait_for_timeout(500)
            await self._select(page, ["#Taluk", "#taluk"], fields["taluk"], "taluk")
            filled.append("taluk")
            if fields.get("locality"):
                try:
                    await self._select(
                        page,
                        ["#PgrsForVillage", "#Village", "#Locality"],
                        fields["locality"],
                        "locality or village",
                    )
                    filled.append("locality")
                except IpgsConnectorError:
                    # Village lists are portal-controlled and frequently use a
                    # different code or spelling. Leave that choice visible to
                    # the resident rather than guessing a location.
                    manual_fields.append("locality")
            await self._fill(page, ["#Address", "#address"], fields["address"], "address")
            filled.append("address")
            await self._fill(page, ["#pincode", "#Pincode"], fields["pincode"], "PIN code")
            filled.append("pincode")

            # Move to the classification panel without making a submission.
            # If the portal keeps the resident on step one because a village
            # choice is still needed, the open page remains available for that
            # manual correction.
            stage = "grievance and location"
            next_button = await self._locator(
                page, ["button:has-text('ಮುಂದಕ್ಕೆ')", "button:has-text('Next')"]
            )
            if next_button is not None:
                try:
                    await next_button.click()
                    await page.wait_for_timeout(500)
                except Exception:
                    pass
            classification = await self._locator(
                page, ["#summary_id", "#Summary", "#summary"]
            )
            if classification is not None and await classification.is_visible():
                stage = "grievance classification"

            optional_selectors = {
                "summary_id": ["#Summary", "#summary", "#summary_id"],
                "department_id": ["#Department", "#DepartmentId", "#dept_id"],
                "category_id": ["#Category", "#CategoryId", "#category_id"],
                "sub_category_id": ["#SubCategory", "#SubCategoryId", "#sub_categ1_id"],
                "sub_category_2_id": ["#sub_categ2_id"],
                "sub_category_3_id": ["#sub_categ3_id"],
                "sub_category_4_id": ["#sub_categ4_id"],
                "officer_post_id": ["#OfficerPostId", "#officer_post_id"],
            }
            for field_name, selectors in optional_selectors.items():
                if fields.get(field_name) and await self._locator(page, selectors):
                    try:
                        if not await (await self._locator(page, selectors)).is_visible():
                            manual_fields.append(field_name)
                            continue
                        await self._select(page, selectors, fields[field_name], field_name)
                        filled.append(field_name)
                    except IpgsConnectorError:
                        manual_fields.append(field_name)

            attachment_receipt: list[str] = []
            if preparation.attachment_ids and ticket_id:
                attachment_paths: list[Path] = []
                attachment_names: list[str] = []
                for attachment_id in preparation.attachment_ids:
                    attachment = community.get_attachment(owner_id, attachment_id)
                    path_method = getattr(community, "attachment_path", None)
                    if path_method is None:
                        continue
                    path = Path(path_method(attachment))
                    if path.exists():
                        attachment_paths.append(path)
                        attachment_names.append(attachment.filename)
                if attachment_paths and await self._set_files(page, attachment_paths):
                    attachment_receipt = attachment_names
                    filled.append("evidence")

            # The mobile number is retained only in the approved preparation
            # and is never copied into the run receipt.
            try:
                await self._fill(
                    page,
                    ["#UserMobileNumber", "#MobileNumber", "#mobile"],
                    fields["mobile"],
                    "mobile number",
                )
                filled.append("mobile")
            except IpgsConnectorError:
                # The citizen-details panel can be hidden until the portal's
                # Next step. The approved value remains available in memory;
                # the resident can enter it in the visible panel.
                manual_fields.append("mobile")

            session = _BrowserSession(
                owner_id=owner_id,
                run_id=run_id,
                ticket_id=ticket_id,
                content_hash=preparation.content_hash,
                page=page,
                browser=browser,
                playwright=playwright,
                approved_fields=dict(preparation.fields),
                filled_fields=filled,
            )
            async with self._lock:
                old = self._sessions.pop(key, None)
                self._sessions[key] = session
            if old:
                await self._close_session(old)
            receipt = {
                "connector_id": IPGRS_CONNECTOR_ID,
                "portal_url": settings.ipgrs_browser_url,
                "stage": stage,
                "filled_fields": filled,
                "manual_fields": manual_fields,
                "attachment_filenames": attachment_receipt,
                "content_hash": preparation.content_hash,
                "otp_required": True,
                "captcha_expected": True,
            }
            return self._community_update(
                community,
                owner_id,
                run_id,
                status=AgentRunStatus.WAITING_FOR_USER,
                connector_id=IPGRS_CONNECTOR_ID,
                message=(
                    "The official Karnataka iPGRS form is open and the approved fields are filled. "
                    "Choose the service classification, complete any CAPTCHA, and use resume_run "
                    "to request OTP or continue after verification. No complaint has been "
                    "submitted."
                ),
                receipt=receipt,
            )
        except Exception as exc:
            if browser is not None or playwright is not None:
                await self._close_session(
                    _BrowserSession(
                        owner_id=owner_id,
                        run_id=run_id,
                        ticket_id=ticket_id,
                        content_hash=preparation.content_hash,
                        page=None,
                        browser=browser,
                        playwright=playwright,
                    )
                )
            return self._community_update(
                community,
                owner_id,
                run_id,
                status=AgentRunStatus.FAILED,
                message=f"The iPGRS form could not be prepared: {exc}",
                connector_id=IPGRS_CONNECTOR_ID,
            )

    async def resume(
        self,
        *,
        owner_id: str,
        run_id: str,
        verification_code: str | None,
        send_otp: bool,
        submit: bool,
        resident_attestation: bool,
        community: Any,
    ) -> AgentRun:
        key = (owner_id, run_id)
        async with self._lock:
            session = self._sessions.get(key)
        if session is None:
            # Browser processes are intentionally not persisted. Reconstruct
            # the approved form from the durable run/preparation checkpoint so
            # a service restart does not lose the resident's work; OTP and
            # CAPTCHA must still be completed again in the new browser.
            try:
                run = community.get_run(owner_id, run_id)
                if not run.ticket_id:
                    raise IpgsConnectorError("The filing run has no ticket checkpoint")
                detail = community.get_ticket(owner_id, run.ticket_id)
                preparation = community.latest_preparation(owner_id, detail.ticket.id)
                if preparation is None or preparation.status != "approved":
                    raise IpgsConnectorError(
                        "The approved preparation is no longer available; prepare and approve again"
                    )
                if preparation.approval_expires_at is not None:
                    from .community_store import utc_now

                    if preparation.approval_expires_at <= utc_now():
                        raise IpgsConnectorError(
                            "The review approval has expired; prepare and approve again"
                        )
                return await self.start(
                    owner_id=owner_id,
                    run_id=run_id,
                    ticket_id=detail.ticket.id,
                    preparation=preparation,
                    community=community,
                    settings=get_settings(),
                )
            except Exception as exc:
                return self._community_update(
                    community,
                    owner_id,
                    run_id,
                    status=AgentRunStatus.FAILED,
                    message=f"The supervised browser session could not be recovered: {exc}",
                )

        page = session.page
        if submit and not resident_attestation:
            return self._community_update(
                community,
                owner_id,
                run_id,
                status=AgentRunStatus.READY_FOR_REVIEW,
                message=(
                    "Final submission is paused. Confirm in the open portal that the "
                    "classification and CAPTCHA/consent steps are complete, then retry "
                    "with the resident final-review attestation."
                ),
                receipt={
                    "connector_id": IPGRS_CONNECTOR_ID,
                    "content_hash": session.content_hash,
                    "stage": "resident attestation required",
                },
            )
        try:
            if send_otp:
                await self._click(
                    page,
                    ["#btnOtpGenerate", "button:has-text('Generate OTP')"],
                    "OTP",
                )
                session.otp_requested = True
                await page.wait_for_timeout(500)

            if verification_code:
                await self._fill(page, ["#OTP", "#otp"], verification_code, "OTP")
                verify = await self._locator(
                    page,
                    ["#btnSubmitOTPverify", "button:has-text('Verify OTP')"],
                )
                if verify is None:
                    raise IpgsConnectorError("The iPGRS OTP verification button is not available")
                await self._click(
                    page,
                    ["#btnSubmitOTPverify", "button:has-text('Verify OTP')"],
                    "OTP verification",
                )
                await page.wait_for_timeout(700)
                session.mobile_verified = await self._detect_mobile_verified(page)
                if not session.mobile_verified:
                    return self._community_update(
                        community,
                        owner_id,
                        run_id,
                        status=AgentRunStatus.WAITING_FOR_USER,
                        message=(
                            "The portal did not confirm the OTP. Check the code and resume again."
                        ),
                        receipt={"connector_id": IPGRS_CONNECTOR_ID, "otp_verified": False},
                    )

            if not submit:
                return self._community_update(
                    community,
                    owner_id,
                    run_id,
                    status=AgentRunStatus.READY_FOR_REVIEW,
                    message=(
                        "The portal is ready for the resident's final review. Confirm the "
                        "classification and CAPTCHA in the open browser, then resume with "
                        "submit=true."
                    ),
                    receipt={
                        "connector_id": IPGRS_CONNECTOR_ID,
                        "otp_requested": session.otp_requested,
                        "otp_verified": session.mobile_verified,
                        "stage": "ready for final submission",
                    },
                )

            if not session.mobile_verified:
                session.mobile_verified = await self._detect_mobile_verified(page)
            if not session.mobile_verified:
                return self._community_update(
                    community,
                    owner_id,
                    run_id,
                    status=AgentRunStatus.WAITING_FOR_USER,
                    message=(
                        "Verify the resident mobile OTP in the portal before requesting final "
                        "submission."
                    ),
                    receipt={"connector_id": IPGRS_CONNECTOR_ID, "otp_verified": False},
                )

            mismatches = await self._approved_text_field_mismatches(
                page, session.approved_fields
            )
            if mismatches:
                return self._community_update(
                    community,
                    owner_id,
                    run_id,
                    status=AgentRunStatus.READY_FOR_REVIEW,
                    message=(
                        "The visible portal fields no longer match the approved payload "
                        f"({', '.join(mismatches)}). Re-prepare and approve the filing "
                        "before retrying."
                    ),
                    receipt={
                        "connector_id": IPGRS_CONNECTOR_ID,
                        "content_hash": session.content_hash,
                        "stage": "approved payload changed",
                        "mismatched_fields": mismatches,
                    },
                )

            dialog_messages: list[str] = []

            async def on_dialog(dialog: Any) -> None:
                dialog_messages.append(dialog.message)
                await dialog.accept()

            page.on("dialog", on_dialog)
            await self._click(
                page,
                ["#subgrie", "button:has-text('Submit')"],
                "final submit",
            )
            await page.wait_for_timeout(1_500)
            body = await page.locator("body").inner_text()
            reference = self._extract_grievance_id("\n".join([body, *dialog_messages]))
            if not reference:
                community.record_outcome(
                    owner_id,
                    session.ticket_id,
                    status=TicketStatus.OUTCOME_UNKNOWN,
                    submitted_content_hash=session.content_hash,
                    note="The portal response did not contain a verifiable grievance number",
                )
                return self._community_update(
                    community,
                    owner_id,
                    run_id,
                    status=AgentRunStatus.FAILED,
                    message=(
                        "The portal response did not expose a verifiable grievance number. "
                        "The outcome is recorded as unknown; do not retry until the portal is "
                        "checked."
                    ),
                    receipt={
                        "connector_id": IPGRS_CONNECTOR_ID,
                        "outcome": "outcome_unknown",
                        "portal_messages": dialog_messages,
                    },
                )

            receipt_policy = PolicyEngine(get_settings().opa_url).decide(
                "record_receipt",
                {
                    "submission_started": True,
                    "receipt_verified": True,
                    "content_hash_match": bool(session.content_hash),
                },
            )
            if not receipt_policy.allow:
                community.record_outcome(
                    owner_id,
                    session.ticket_id,
                    status=TicketStatus.OUTCOME_UNKNOWN,
                    submitted_content_hash=session.content_hash,
                    note="Receipt rejected by submission policy",
                )
                return self._community_update(
                    community,
                    owner_id,
                    run_id,
                    status=AgentRunStatus.FAILED,
                    message=receipt_policy.reason,
                    receipt={
                        "connector_id": IPGRS_CONNECTOR_ID,
                        "outcome": "outcome_unknown",
                        "content_hash": session.content_hash,
                        "policy": receipt_policy.as_dict(),
                    },
                )

            acknowledgement = "Karnataka iPGRS returned a grievance reference after submission."
            community.record_outcome(
                owner_id,
                session.ticket_id,
                status=TicketStatus.SUBMITTED,
                external_reference_id=reference,
                acknowledgement=acknowledgement,
                submitted_content_hash=session.content_hash,
                note="Verified iPGRS grievance reference",
            )
            result = self._community_update(
                community,
                owner_id,
                run_id,
                status=AgentRunStatus.SUBMITTED,
                external_reference_id=reference,
                message="Karnataka iPGRS confirmed the complaint submission",
                receipt={
                    "connector_id": IPGRS_CONNECTOR_ID,
                    "reference_id": reference,
                    "acknowledgement": acknowledgement,
                    "content_hash": session.content_hash,
                    "policy": receipt_policy.as_dict(),
                    "portal_messages": dialog_messages,
                },
            )
            async with self._lock:
                self._sessions.pop(key, None)
            await self._close_session(session)
            return result
        except Exception as exc:
            return self._community_update(
                community,
                owner_id,
                run_id,
                status=AgentRunStatus.WAITING_FOR_USER,
                message=f"The iPGRS step needs attention in the open browser: {exc}",
                receipt={
                    "connector_id": IPGRS_CONNECTOR_ID,
                    "stage": "portal interaction required",
                },
            )

    async def cancel(self, *, owner_id: str, run_id: str) -> bool:
        key = (owner_id, run_id)
        async with self._lock:
            session = self._sessions.pop(key, None)
        if session is None:
            return False
        await self._close_session(session)
        return True

    async def close_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await self._close_session(session)


ipgrs_browser_manager = IpgsBrowserManager()


async def close_ipgrs_sessions() -> None:
    await ipgrs_browser_manager.close_all()
