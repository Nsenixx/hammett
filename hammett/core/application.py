"""The module contains the implementation of the high-level application class."""

from typing import TYPE_CHECKING, Any

from telegram import Update
from telegram.ext import Application as NativeApplication
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from hammett.core.conversation_handler import ConversationHandler
from hammett.core.exceptions import TokenIsNotSpecified, UnknownHandlerType
from hammett.core.handlers import calc_checksum, log_unregistered_handler
from hammett.core.permissions import apply_permission_to
from hammett.types import HandlerAlias, HandlerType
from hammett.utils.log import configure_logging

if TYPE_CHECKING:
    from collections.abc import Iterable

    from telegram.ext import BasePersistence
    from telegram.ext._utils.types import BD, CD, UD
    from typing_extensions import Self

    from hammett.core.screen import Screen, StartScreen
    from hammett.types import Handler, NativeStates, State, States

__all__ = ('Application', )


class Application:
    """The class is a wrapper for the native Application class.
    The wrapping solves the following tasks:
    - hiding low-level technical details of python-telegram-bot from developers;
    - registering handlers;
    - configuring logging.
    """

    def __init__(
        self: 'Self',
        name: str,
        *,
        entry_point: 'type[StartScreen]',
        error_handlers: 'list[Handler] | None' = None,
        job_queue_handlers: 'list[dict[str, Any]] | None' = None,
        native_states: 'NativeStates | None' = None,
        persistence: 'BasePersistence[UD, CD, BD] | None' = None,
        states: 'States | None' = None,
    ) -> None:
        from hammett.conf import settings

        if not settings.TOKEN:
            raise TokenIsNotSpecified

        self._setup()

        self._route_handlers = ('sgoto', 'sjump')
        self._builtin_handlers = ('goto', 'jump', 'start', *self._route_handlers)
        self._entry_point = entry_point()
        self._name = name
        self._native_states = native_states or {}
        self._states = states

        builder = NativeApplication.builder().token(settings.TOKEN)
        if persistence:
            builder.persistence(persistence)

        self._native_application = builder.build()

        if self._states:
            for state in self._states.items():
                self._register_handlers(*state)

        self._register_error_handlers(error_handlers)
        self._register_job_queue_handlers(job_queue_handlers)

        start_handler = apply_permission_to(self._entry_point.start)
        self._native_application.add_handler(ConversationHandler(
            entry_points=[CommandHandler('start', start_handler)],
            states=self._native_states,
            fallbacks=[CommandHandler('start', start_handler)],
            name=self._name,
            persistent=bool(persistence),
        ))

    @staticmethod
    def _get_handler_object(
        handler: 'HandlerAlias',
        handler_type: 'Any | str | None',
        possible_handler: 'Handler',
    ) -> CallbackQueryHandler[Any] | MessageHandler[Any]:
        """Returns the handler object depending on its type."""

        handler_object: CallbackQueryHandler[Any] | MessageHandler[Any]
        if handler_type in (HandlerType.BUTTON_HANDLER, ''):
            handler_object = CallbackQueryHandler(
                apply_permission_to(handler),
                # Specify a pattern. The pattern is used to determine which handler
                # should be triggered when a specific button is pressed.
                pattern=calc_checksum(handler),
            )
        elif handler_type == HandlerType.COMMAND_HANDLER:
            handler_object = MessageHandler(
                filters.COMMAND & filters.Regex(f'^/{possible_handler.command_name}'),
                handler,
            )
        elif handler_type == HandlerType.INPUT_HANDLER:
            handler_object = MessageHandler(
                possible_handler.filters,  # type: ignore[arg-type]
                handler,
            )
        elif handler_type == HandlerType.TYPING_HANDLER:
            handler_object = MessageHandler(
                filters.TEXT & (~filters.COMMAND),
                handler,
            )
        else:
            raise UnknownHandlerType

        return handler_object

    def _register_error_handlers(
        self: 'Self',
        error_handlers: 'list[Handler] | None',
    ) -> None:
        """Registers the specified error handlers."""

        if error_handlers:
            for error_handler in error_handlers:
                self._native_application.add_error_handler(error_handler)  # type: ignore[arg-type]

    def _register_job_queue_handlers(
        self: 'Self',
        job_queue_handlers: 'list[dict[str, Any]] | None' = None,
    ) -> None:
        """Registers the specified job queue handlers."""

        if job_queue_handlers:
            job_queue = self._native_application.job_queue
            for job_queue_handler in job_queue_handlers:
                handler = job_queue_handler['handler']
                first_request = job_queue_handler['first_request']
                interval_request = job_queue_handler['interval_request']

                if job_queue:
                    job_queue.run_repeating(
                        handler,
                        first=first_request,
                        interval=interval_request,
                    )

    def _register_handlers(self: 'Self', state: 'State', screens: 'Iterable[type[Screen]]') -> None:
        self._set_default_value_to_native_states(state)

        for screen in screens:
            instance = screen()
            for name in dir(instance):
                acceptable_handler_types = (
                    HandlerType.BUTTON_HANDLER,
                    HandlerType.COMMAND_HANDLER,
                    HandlerType.INPUT_HANDLER,
                    HandlerType.TYPING_HANDLER,
                )
                handler, handler_type = None, None
                possible_handler = getattr(instance, name)
                possible_handler_type = getattr(possible_handler, 'handler_type', '')
                if (
                    name in self._builtin_handlers or
                    possible_handler_type in acceptable_handler_types
                ):
                    handler, handler_type = possible_handler, possible_handler_type

                if handler is None:
                    log_unregistered_handler(possible_handler)
                    continue

                handler_object = self._get_handler_object(handler, handler_type, possible_handler)

                if name in self._route_handlers and instance.routes:
                    for route in instance.routes:
                        route_states, _ = route
                        for route_state in route_states:
                            self._set_default_value_to_native_states(route_state)
                            self._native_states[route_state].append(handler_object)
                else:
                    self._native_states[state].append(handler_object)

    def _set_default_value_to_native_states(self: 'Self', state: 'State') -> None:
        """Sets default value to native states."""

        try:
            self._native_states[state]
        except KeyError:
            self._native_states[state] = []

    def _setup(self: 'Self') -> None:
        """Configures logging."""

        from hammett.conf import settings
        configure_logging(settings.LOGGING)

    def run(self: 'Self') -> None:
        """Runs the application."""

        from hammett.conf import settings

        if settings.USE_WEBHOOK:
            self._native_application.run_webhook(
                listen=settings.WEBHOOK_LISTEN,
                port=settings.WEBHOOK_PORT,
                url_path=settings.WEBHOOK_URL_PATH,
                webhook_url=settings.WEBHOOK_URL,
                allowed_updates=Update.ALL_TYPES,
            )
        else:
            self._native_application.run_polling(allowed_updates=Update.ALL_TYPES)
