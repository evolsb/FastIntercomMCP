"""Intercom API client with intelligent sync capabilities and performance optimization."""

import logging
import time
from collections.abc import Callable
from datetime import datetime

try:
    from datetime import UTC
except ImportError:
    # Python < 3.11 compatibility
    from datetime import timezone

    UTC = timezone.utc
from typing import Any

import httpx

from .models import Conversation, Message, SyncStats
from .transport.optimization import APIOptimizer, OptimizationConfig
from .transport.rate_limiter import AdaptiveRateLimiter, RateLimitConfig

logger = logging.getLogger(__name__)


class IntercomClient:
    """Enhanced Intercom API client with performance optimization and intelligent rate limiting."""

    def __init__(
        self,
        access_token: str,
        timeout: int = 300,
        rate_limit_config: RateLimitConfig = None,
        optimization_config: OptimizationConfig = None,
    ):
        self.access_token = access_token
        self.timeout = timeout
        self.base_url = "https://api.intercom.io"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._app_id = None

        # Enhanced rate limiting and optimization
        self.rate_limiter = AdaptiveRateLimiter(rate_limit_config or RateLimitConfig())
        self.optimizer = APIOptimizer(optimization_config or OptimizationConfig())

        # Legacy rate limiting (for backward compatibility)
        self._request_times = []
        self._max_requests_per_window = 80  # Be conservative
        self._window_seconds = 10

        # Performance monitoring
        self._performance_callbacks: list[Callable] = []

    def add_performance_callback(self, callback: Callable):
        """Add a callback for performance monitoring."""
        self._performance_callbacks.append(callback)
        self.rate_limiter.add_performance_callback(callback)

    async def get_app_id(self) -> str | None:
        """Get the Intercom app ID for generating conversation URLs."""
        if self._app_id:
            return self._app_id

        try:
            # Use optimized request with caching
            cache_key = "app_id"
            data = await self._make_optimized_request(
                "GET",
                f"{self.base_url}/me",
                cache_key=cache_key,
                cache_ttl=3600,  # Cache for 1 hour
                priority="high",
            )

            if data.get("app") and data["app"].get("id_code"):
                self._app_id = str(data["app"]["id_code"])
                logger.info(f"Retrieved app ID: {self._app_id}")
                return self._app_id
        except Exception as e:
            logger.warning(f"Failed to get app ID: {e}")

        return None

    async def _make_optimized_request(
        self,
        method: str,
        url: str,
        data: Any = None,
        cache_key: str = None,
        cache_ttl: int = None,
        priority: str = "normal",
    ) -> Any:
        """Make an optimized API request with rate limiting and caching."""
        # Apply intelligent rate limiting
        await self.rate_limiter.acquire(priority)

        try:
            # Use optimized request
            result = await self.optimizer.make_request(
                method=method,
                url=url,
                headers=self.headers,
                data=data,
                cache_key=cache_key,
                cache_ttl=cache_ttl,
                priority=priority,
                timeout=self.timeout,
            )

            # Report successful request for adaptive learning
            self.rate_limiter.report_successful_request()

            return result

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:  # Rate limit hit
                retry_after = e.response.headers.get("Retry-After")
                retry_seconds = float(retry_after) if retry_after else None
                self.rate_limiter.report_rate_limit_hit(retry_seconds)

                # Re-raise to let caller handle
                raise
            raise

    async def _rate_limit(self):
        """Legacy rate limiting method for backward compatibility."""
        # Use the new adaptive rate limiter
        await self.rate_limiter.acquire("normal")

    async def fetch_conversations_incremental(
        self,
        since_timestamp: datetime,
        until_timestamp: datetime | None = None,
        progress_callback: Callable | None = None,
    ) -> SyncStats:
        """Fetch conversations that have been updated since the given timestamp.

        Args:
            since_timestamp: Fetch conversations updated after this time
            until_timestamp: Optional upper bound for updated_at
            progress_callback: Optional callback for progress updates

        Returns:
            SyncStats with information about what was synced
        """
        start_time = time.time()
        conversations = []
        api_calls = 0

        async with httpx.AsyncClient(timeout=self.timeout):
            # Build search filters
            search_filters = [
                {
                    "field": "updated_at",
                    "operator": ">",
                    "value": int(since_timestamp.timestamp()),
                }
            ]

            if until_timestamp:
                search_filters.append(
                    {
                        "field": "updated_at",
                        "operator": "<",
                        "value": int(until_timestamp.timestamp()),
                    }
                )

            # Build query
            if len(search_filters) == 1:
                query = search_filters[0]
            else:
                query = {"operator": "AND", "value": search_filters}

            # Paginate through results
            page = 1
            per_page = 150  # Max for search API
            total_found = 0

            while True:
                request_body = {
                    "query": query,
                    "pagination": {"per_page": per_page, "page": page},
                    "sort": {"field": "updated_at", "order": "desc"},
                }

                logger.debug(f"Fetching incremental page {page}")

                # Use optimized request with caching for search results
                cache_key = f"search_incremental_{hash(str(request_body))}"
                data = await self._make_optimized_request(
                    "POST",
                    f"{self.base_url}/conversations/search",
                    data=request_body,
                    cache_key=cache_key,
                    cache_ttl=60,  # Cache search results for 1 minute
                    priority="high",
                )
                api_calls += 1
                page_conversations = data.get("conversations", [])

                if not page_conversations:
                    break

                # Parse conversations
                for conv_data in page_conversations:
                    conversation = self._parse_conversation_from_search(conv_data)
                    if conversation:
                        conversations.append(conversation)

                total_found += len(page_conversations)

                if progress_callback:
                    await progress_callback(f"Fetched {total_found} conversations...")

                if len(page_conversations) < per_page:
                    break

                page += 1

        elapsed_time = time.time() - start_time
        logger.info(
            f"Incremental sync complete: {len(conversations)} conversations "
            f"in {elapsed_time:.2f}s ({api_calls} API calls)"
        )

        return SyncStats(
            total_conversations=len(conversations),
            new_conversations=len(conversations),  # All are new in incremental
            updated_conversations=0,  # None are updated in incremental
            total_messages=sum(len(conv.messages) for conv in conversations),
            duration_seconds=elapsed_time,
            api_calls_made=api_calls,
        )

    async def fetch_conversations_for_period(
        self,
        start_date: datetime,
        end_date: datetime,
        progress_callback: Callable | None = None,
    ) -> list[Conversation]:
        """Fetch all conversations for a specific time period.

        Args:
            start_date: Start of time period
            end_date: End of time period
            progress_callback: Optional progress callback

        Returns:
            List of conversations in the period
        """
        conversations = []

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # Use updated_at to capture both new conversations AND existing
            # conversations with new messages
            search_filters = [
                {
                    "field": "updated_at",
                    "operator": ">",
                    "value": int(start_date.timestamp()),
                },
                {
                    "field": "updated_at",
                    "operator": "<",
                    "value": int(end_date.timestamp()),
                },
            ]

            # Paginate through results using cursor-based pagination
            per_page = 150  # Max allowed by Intercom search API

            # Human-readable search initiation
            start_str = start_date.strftime("%b %d, %Y")
            end_str = end_date.strftime("%b %d, %Y")
            logger.info(f"🔍 Searching for conversations UPDATED between {start_str} and {end_str}")
            logger.info(
                "    📝 This includes: NEW conversations + existing ones with recent activity "
                "(tags, assignments, etc.)"
            )
            logger.info(
                f"    🔄 Making single API search query, then paging through results "
                f"{per_page} at a time"
            )

            # Detailed DEBUG search info
            logger.debug(
                f"SEARCH_INIT filters={search_filters} start_date={start_date} "
                f"end_date={end_date} start_timestamp={int(start_date.timestamp())} "
                f"end_timestamp={int(end_date.timestamp())}"
            )
            cursor = None
            page_num = 1  # For logging purposes only
            seen_conversation_ids = set()  # Deduplication safety check

            while True:
                await self._rate_limit()

                # Build pagination object - use cursor if available, otherwise start from beginning
                pagination = {"per_page": per_page}
                if cursor:
                    pagination["starting_after"] = cursor

                request_body = {
                    "query": {"operator": "AND", "value": search_filters},
                    "pagination": pagination,
                    "sort": {"field": "updated_at", "order": "asc"},
                }

                # Human-readable INFO logging
                page_info = f"page {page_num}" + (" (continuing)" if cursor else "")
                logger.info(f"📄 Fetching {page_info}...")

                # Detailed DEBUG logging for machines
                cursor_info = f"cursor={cursor[:20]}..." if cursor else "initial_page"
                logger.debug(
                    f"API_REQUEST page={page_num} cursor_info={cursor_info} "
                    f"start_date={start_date} end_date={end_date}"
                )

                response = await client.post(
                    f"{self.base_url}/conversations/search",
                    headers=self.headers,
                    json=request_body,
                )
                response.raise_for_status()

                data = response.json()
                page_conversations = data.get("conversations", [])
                total_count = data.get("total_count", "unknown")
                pagination_info = data.get("pages", {})

                # Human-readable response logging
                if page_num == 1:
                    logger.info(
                        f"📊 Intercom API reports: {total_count} conversations match our "
                        "search criteria"
                    )
                    logger.info(
                        "    🔍 Search criteria: conversations with ANY activity in date range"
                    )
                    logger.info(
                        f"    📥 Fetching these {total_count} conversations in pages of {per_page}"
                    )

                # Calculate progress
                conversations_fetched_so_far = (page_num - 1) * per_page + len(page_conversations)
                progress_pct = (
                    (conversations_fetched_so_far / total_count * 100) if total_count > 0 else 0
                )

                logger.info(
                    f"📥 Page {page_num}: Retrieved {len(page_conversations)} conversations "
                    f"({conversations_fetched_so_far}/{total_count} - {progress_pct:.1f}%)"
                )

                # Detailed DEBUG response logging
                logger.debug(
                    f"API_RESPONSE page={page_num} "
                    f"conversations_returned={len(page_conversations)} total_count={total_count}"
                )

                # Extract cursor for next page
                next_cursor = (
                    pagination_info.get("next", {}).get("starting_after")
                    if pagination_info
                    else None
                )
                logger.debug(f"Next cursor: {next_cursor[:20] + '...' if next_cursor else 'None'}")

                if not page_conversations:
                    break

                # Parse conversations with duplicate detection
                logger.debug(
                    f"PARSING_START page={page_num} conversation_count={len(page_conversations)}"
                )
                parsed_count = 0
                filtered_count = 0
                duplicate_count = 0
                date_counts = {}

                for conv_data in page_conversations:
                    # Check for duplicates (safety measure)
                    conv_id = conv_data.get("id")
                    if conv_id in seen_conversation_ids:
                        duplicate_count += 1
                        continue
                    seen_conversation_ids.add(conv_id)

                    # Track what dates we're seeing
                    updated_ts = conv_data.get("updated_at", 0)
                    if updated_ts:
                        updated_date = datetime.fromtimestamp(updated_ts, tz=UTC).date()
                        date_counts[updated_date] = date_counts.get(updated_date, 0) + 1

                    conversation = self._parse_conversation_from_search(conv_data)
                    if conversation:
                        conversations.append(conversation)
                        parsed_count += 1
                    else:
                        filtered_count += 1

                # Human-readable processing summary
                date_summary = ", ".join(
                    [
                        f"{date.strftime('%b %d')}: {count}"
                        for date, count in sorted(date_counts.items())
                    ]
                )
                logger.info(
                    f"📊 Processed: {parsed_count} kept, {filtered_count} filtered ({date_summary})"
                )

                # Detailed DEBUG processing info
                logger.debug(
                    f"PROCESSING_RESULT page={page_num} parsed_count={parsed_count} "
                    f"filtered_count={filtered_count} duplicate_count={duplicate_count} "
                    f"date_distribution={dict(sorted(date_counts.items()))}"
                )
                if duplicate_count > 0:
                    logger.warning(
                        f"⚠️  Detected {duplicate_count} duplicate conversations "
                        "(possible pagination issue)"
                    )
                    logger.debug(
                        f"DUPLICATE_DETECTION page={page_num} duplicate_count={duplicate_count}"
                    )

                if progress_callback:
                    await progress_callback(
                        f"Fetched {len(conversations)} conversations "
                        f"from {start_date.date()} to {end_date.date()} "
                        f"(page {page_num}, got {len(page_conversations)} in this batch)"
                    )

                # Check if more pages available using cursor
                if not next_cursor or len(page_conversations) < per_page:
                    final_fetched = (page_num - 1) * per_page + len(page_conversations)
                    if not next_cursor:
                        logger.info(
                            f"✅ Search complete: Fetched all {final_fetched}/{total_count} "
                            "conversations (100%)"
                        )
                        logger.debug(f"PAGINATION_END reason=no_next_cursor page={page_num}")
                    else:
                        logger.info(
                            f"✅ Search complete: Final page processed "
                            f"({final_fetched}/{total_count} conversations)"
                        )
                        logger.debug(
                            f"PAGINATION_END reason=partial_page page={page_num} "
                            f"conversations={len(page_conversations)} per_page={per_page}"
                        )
                    break

                logger.info(f"⏭️  Continuing to page {page_num + 1}...")
                logger.debug(
                    f"PAGINATION_CONTINUE from_page={page_num} to_page={page_num + 1} "
                    f"next_cursor={next_cursor[:20] + '...' if next_cursor else 'None'}"
                )
                cursor = next_cursor
                page_num += 1

        # Add summary logging to understand the distribution
        if conversations:
            # Count new vs updated conversations
            new_count = 0
            updated_count = 0
            date_distribution = {}

            for conv in conversations:
                created_date = conv.created_at.date()
                updated_date = conv.updated_at.date()

                # Check if created within our date range
                if start_date.date() <= created_date <= end_date.date():
                    new_count += 1
                else:
                    updated_count += 1

                # Track distribution by updated date
                date_str = updated_date.isoformat()
                date_distribution[date_str] = date_distribution.get(date_str, 0) + 1

            logger.info(
                f"Sync summary for {start_date.date()} to {end_date.date()}: "
                f"Total={len(conversations)}, New={new_count}, Updated={updated_count}"
            )

            # Log date distribution
            logger.info("Conversations by updated date:")
            for date_str in sorted(date_distribution.keys()):
                logger.info(f"  {date_str}: {date_distribution[date_str]} conversations")

        return conversations

    async def count_conversations_by_day(
        self, start_date: datetime, end_date: datetime
    ) -> dict[str, int]:
        """Count conversations for each day in the period.

        Args:
            start_date: Start of time period
            end_date: End of time period

        Returns:
            Dict mapping date strings to conversation counts
        """
        from datetime import timedelta

        daily_counts = {}
        current_date = start_date.date()
        end = end_date.date()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while current_date <= end:
                next_date = current_date + timedelta(days=1)

                # Create search filters for this specific day
                search_filters = [
                    {
                        "field": "updated_at",
                        "operator": ">=",
                        "value": int(
                            datetime.combine(current_date, datetime.min.time()).timestamp()
                        ),
                    },
                    {
                        "field": "updated_at",
                        "operator": "<",
                        "value": int(datetime.combine(next_date, datetime.min.time()).timestamp()),
                    },
                ]

                await self._rate_limit()

                # Just get first page to check total count
                request_body = {
                    "query": {"operator": "AND", "value": search_filters},
                    "pagination": {"per_page": 1, "page": 1},  # Just need count
                }

                response = await client.post(
                    f"{self.base_url}/conversations/search",
                    headers=self.headers,
                    json=request_body,
                )
                response.raise_for_status()

                data = response.json()
                total_count = data.get("total_count", 0)
                daily_counts[current_date.strftime("%Y-%m-%d")] = total_count

                current_date = next_date

        return daily_counts

    def _parse_conversation_from_search(self, conv_data: dict) -> Conversation | None:
        """Parse a conversation from search API response."""
        try:
            # Parse messages
            messages = []
            has_customer_message = False

            # Get conversation parts (messages)
            conversation_parts = conv_data.get("conversation_parts", {})
            if isinstance(conversation_parts, dict):
                parts_list = conversation_parts.get("conversation_parts", [])
            else:
                parts_list = conversation_parts or []

            for part in parts_list:
                if not isinstance(part, dict):
                    continue

                if part.get("part_type") in ["comment", "note", "message"]:
                    if not part.get("body"):
                        continue

                    author_type = (
                        "admin" if part.get("author", {}).get("type") == "admin" else "user"
                    )

                    if author_type == "user":
                        has_customer_message = True

                    message = Message(
                        id=str(part.get("id", "unknown")),
                        author_type=author_type,
                        body=part.get("body", ""),
                        created_at=datetime.fromtimestamp(part.get("created_at", 0), tz=UTC),
                        part_type=part.get("part_type"),
                    )
                    messages.append(message)

            # Add initial message from source if exists
            source = conv_data.get("source", {})
            if isinstance(source, dict) and source.get("body"):
                initial_message = Message(
                    id=conv_data["id"] + "_initial",
                    author_type="user",
                    body=source["body"],
                    created_at=datetime.fromtimestamp(conv_data["created_at"], tz=UTC),
                    part_type="initial",
                )
                messages.insert(0, initial_message)
                has_customer_message = True

            # Skip admin-only conversations
            if not has_customer_message:
                conv_id = conv_data.get("id", "unknown")
                updated_at = datetime.fromtimestamp(conv_data.get("updated_at", 0), tz=UTC)
                logger.debug(
                    f"Filtering out admin-only conversation {conv_id} "
                    f"(updated: {updated_at.date()}) - no customer messages found"
                )
                return None

            # Sort messages by creation time
            messages.sort(key=lambda msg: msg.created_at)

            # Get customer email
            customer_email = None
            if isinstance(source, dict):
                author = source.get("author", {})
                if isinstance(author, dict):
                    customer_email = author.get("email")

            # Parse tags
            tags = []
            tags_data = conv_data.get("tags", {})
            if isinstance(tags_data, dict):
                tags_list = tags_data.get("tags", [])
                for tag in tags_list:
                    if isinstance(tag, dict):
                        tags.append(tag.get("name", str(tag)))
                    else:
                        tags.append(str(tag))

            # Get updated_at - use created_at as fallback
            updated_at = conv_data.get("updated_at", conv_data.get("created_at", 0))

            # Create conversation object
            conversation = Conversation(
                id=conv_data["id"],
                created_at=datetime.fromtimestamp(conv_data["created_at"], tz=UTC),
                updated_at=datetime.fromtimestamp(updated_at, tz=UTC),
                messages=messages,
                customer_email=customer_email,
                tags=tags,
            )

            # Debug logging to understand why we're getting so many conversations
            created_date = conversation.created_at.date()
            updated_date = conversation.updated_at.date()
            days_since_created = (datetime.now(tz=UTC).date() - created_date).days
            days_since_updated = (datetime.now(tz=UTC).date() - updated_date).days

            logger.debug(
                f"Conversation {conversation.id}: "
                f"created={conversation.created_at.isoformat()} ({days_since_created} days ago), "
                f"updated={conversation.updated_at.isoformat()} ({days_since_updated} days ago), "
                f"is_new_today={days_since_created == 0}, "
                f"updated_today={days_since_updated == 0}"
            )

            return conversation

        except Exception as e:
            logger.warning(f"Failed to parse conversation {conv_data.get('id', 'unknown')}: {e}")
            return None

    async def fetch_individual_conversation(self, conversation_id: str) -> Conversation | None:
        """Fetch a complete conversation thread with all messages.

        Args:
            conversation_id: The Intercom conversation ID

        Returns:
            Complete conversation with all messages, or None if not found
        """
        try:
            # Use optimized request with caching
            cache_key = f"conversation_{conversation_id}"
            conv_data = await self._make_optimized_request(
                "GET",
                f"{self.base_url}/conversations/{conversation_id}",
                cache_key=cache_key,
                cache_ttl=300,  # Cache individual conversations for 5 minutes
                priority="high",
            )

            return self._parse_individual_conversation(conv_data)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Conversation {conversation_id} not found")
                return None
            logger.error(f"Failed to fetch conversation {conversation_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch conversation {conversation_id}: {e}")
            return None

    # Alias for backward compatibility
    async def get_conversation_by_id(self, conversation_id: str) -> Conversation | None:
        """Alias for fetch_individual_conversation for backward compatibility."""
        return await self.fetch_individual_conversation(conversation_id)

    async def fetch_individual_conversations(
        self, conversation_ids: list[str], progress_callback: Callable | None = None
    ) -> list[Conversation]:
        """Fetch multiple complete conversation threads.

        Args:
            conversation_ids: List of conversation IDs to fetch
            progress_callback: Optional progress callback

        Returns:
            List of complete conversations
        """
        conversations = []

        for i, conv_id in enumerate(conversation_ids):
            conversation = await self.fetch_individual_conversation(conv_id)
            if conversation:
                conversations.append(conversation)

            if progress_callback:
                await progress_callback(
                    f"Fetching complete threads: {i + 1}/{len(conversation_ids)}"
                )

        logger.info(f"Fetched {len(conversations)} complete conversation threads")
        return conversations

    def _parse_individual_conversation(self, conv_data: dict) -> Conversation | None:
        """Parse a conversation from individual conversation API response."""
        try:
            # Parse messages from conversation_parts
            messages = []
            has_customer_message = False

            # Get conversation parts (messages)
            conversation_parts = conv_data.get("conversation_parts", {})
            if isinstance(conversation_parts, dict):
                parts_list = conversation_parts.get("conversation_parts", [])
            else:
                parts_list = conversation_parts or []

            for part in parts_list:
                if not isinstance(part, dict):
                    continue

                if part.get("part_type") in ["comment", "note", "message"]:
                    if not part.get("body"):
                        continue

                    author_type = (
                        "admin" if part.get("author", {}).get("type") == "admin" else "user"
                    )

                    if author_type == "user":
                        has_customer_message = True

                    message = Message(
                        id=str(part.get("id", "unknown")),
                        author_type=author_type,
                        body=part.get("body", ""),
                        created_at=datetime.fromtimestamp(part.get("created_at", 0), tz=UTC),
                        part_type=part.get("part_type"),
                    )
                    messages.append(message)

            # Add initial message from source if exists
            source = conv_data.get("source", {})
            if isinstance(source, dict) and source.get("body"):
                initial_message = Message(
                    id=conv_data["id"] + "_initial",
                    author_type="user",
                    body=source["body"],
                    created_at=datetime.fromtimestamp(conv_data["created_at"], tz=UTC),
                    part_type="initial",
                )
                messages.insert(0, initial_message)
                has_customer_message = True

            # Skip admin-only conversations
            if not has_customer_message:
                return None

            # Sort messages by creation time to ensure proper ordering
            messages.sort(key=lambda msg: msg.created_at)

            # Deduplicate messages by ID
            seen_ids = set()
            deduplicated_messages = []
            for msg in messages:
                if msg.id not in seen_ids:
                    deduplicated_messages.append(msg)
                    seen_ids.add(msg.id)

            # Get customer email
            customer_email = None
            if isinstance(source, dict):
                author = source.get("author", {})
                if isinstance(author, dict):
                    customer_email = author.get("email")

            # Fallback to contacts if no email in source
            if not customer_email:
                contacts = conv_data.get("contacts", {})
                if isinstance(contacts, dict) and contacts.get("contacts"):
                    contact = contacts["contacts"][0]
                    customer_email = contact.get("email")

            # Parse tags
            tags = []
            tags_data = conv_data.get("tags", {})
            if isinstance(tags_data, dict):
                tags_list = tags_data.get("tags", [])
                for tag in tags_list:
                    if isinstance(tag, dict):
                        tags.append(tag.get("name", str(tag)))
                    else:
                        tags.append(str(tag))

            # Get updated_at - use created_at as fallback
            updated_at = conv_data.get("updated_at", conv_data.get("created_at", 0))

            return Conversation(
                id=conv_data["id"],
                created_at=datetime.fromtimestamp(conv_data["created_at"], tz=UTC),
                updated_at=datetime.fromtimestamp(updated_at, tz=UTC),
                messages=deduplicated_messages,
                customer_email=customer_email,
                tags=tags,
            )

        except Exception as e:
            logger.warning(
                f"Failed to parse individual conversation {conv_data.get('id', 'unknown')}: {e}"
            )
            return None

    # Alias for backward compatibility
    def _parse_conversation_from_api(self, conv_data: dict) -> Conversation | None:
        """Alias for _parse_individual_conversation for backward compatibility."""
        return self._parse_individual_conversation(conv_data)

    async def get_conversation_messages(
        self,
        conversation_id: str,
        per_page: int = 20,
        starting_after: str | None = None,
    ) -> tuple[list[Message], str | None]:
        """Fetch messages for a conversation with pagination.

        Args:
            conversation_id: The Intercom conversation ID
            per_page: Number of messages per page (max 50)
            starting_after: Cursor for pagination

        Returns:
            Tuple of (messages, next_cursor) where next_cursor is None if no more pages
        """
        await self._rate_limit()

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                params = {"per_page": min(per_page, 50)}
                if starting_after:
                    params["starting_after"] = starting_after

                response = await client.get(
                    f"{self.base_url}/conversations/{conversation_id}/conversation_parts",
                    headers=self.headers,
                    params=params,
                )

                if response.status_code == 404:
                    logger.warning(f"Conversation {conversation_id} not found")
                    return [], None

                response.raise_for_status()
                data = response.json()

                messages = []
                conversation_parts = data.get("conversation_parts", [])

                for part in conversation_parts:
                    message = self._parse_message_from_part(part)
                    if message:
                        messages.append(message)

                # Get next cursor for pagination
                pages = data.get("pages", {})
                next_cursor = (
                    pages.get("next", {}).get("starting_after") if pages.get("next") else None
                )

                return messages, next_cursor

        except Exception as e:
            logger.error(f"Failed to fetch messages for conversation {conversation_id}: {e}")
            return [], None

    def _parse_message_from_part(self, part: dict) -> Message | None:
        """Parse a message from a conversation part."""
        try:
            if not isinstance(part, dict):
                return None

            # Only process actual message parts
            if part.get("part_type") not in ["comment", "note", "message"]:
                return None

            if not part.get("body"):
                return None

            author_type = "admin" if part.get("author", {}).get("type") == "admin" else "user"

            return Message(
                id=str(part.get("id", "unknown")),
                author_type=author_type,
                body=part.get("body", ""),
                created_at=datetime.fromtimestamp(part.get("created_at", 0), tz=UTC),
                part_type=part.get("part_type"),
            )

        except Exception as e:
            logger.warning(f"Failed to parse message part: {e}")
            return None

    async def fetch_complete_conversation_thread(self, conversation_id: str) -> Conversation | None:
        """Fetch a complete conversation with all messages using pagination.

        Args:
            conversation_id: The Intercom conversation ID

        Returns:
            Complete conversation with all messages
        """
        # First get conversation metadata
        conversation = await self.get_conversation_by_id(conversation_id)
        if not conversation:
            return None

        # Then fetch all messages using pagination
        all_messages = []
        next_cursor = None

        while True:
            messages, next_cursor = await self.get_conversation_messages(
                conversation_id, per_page=50, starting_after=next_cursor
            )

            all_messages.extend(messages)

            if not next_cursor:
                break

        # Update conversation with complete message list
        conversation.messages = sorted(all_messages, key=lambda m: m.created_at)
        return conversation

    async def test_connection(self) -> bool:
        """Test if the API connection is working."""
        try:
            # Use optimized request for connection test
            await self._make_optimized_request("GET", f"{self.base_url}/me", priority="high")
            return True
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False

    def get_performance_stats(self) -> dict[str, Any]:
        """Get comprehensive performance statistics."""
        rate_limit_stats = self.rate_limiter.get_stats()
        optimization_stats = self.optimizer.get_performance_stats()

        return {
            "rate_limiting": rate_limit_stats,
            "optimization": optimization_stats,
            "api_client": {
                "base_url": self.base_url,
                "app_id": self._app_id,
                "optimization_enabled": True,
                "adaptive_rate_limiting": True,
            },
        }

    def get_recommendations(self) -> list[str]:
        """Get performance optimization recommendations."""
        recommendations = []

        rate_limit_stats = self.rate_limiter.get_stats()
        optimization_stats = self.optimizer.get_performance_stats()

        # Add rate limiting recommendations
        if "recommendations" in rate_limit_stats:
            recommendations.extend(rate_limit_stats["recommendations"])

        # Add optimization recommendations
        if "recommendations" in optimization_stats:
            recommendations.extend(optimization_stats["recommendations"])

        # Add client-specific recommendations
        current_efficiency = rate_limit_stats.get("performance", {}).get(
            "efficiency_percentage", 100
        )
        if current_efficiency < 70:
            recommendations.append("API efficiency is low - consider reducing concurrent requests")

        cache_hit_ratio = optimization_stats.get("performance", {}).get("cache_hit_ratio", 0)
        if cache_hit_ratio < 0.2:
            recommendations.append("Low cache usage - verify cache keys and TTL settings")

        return recommendations

    async def close(self):
        """Clean up client resources."""
        await self.optimizer.close()
