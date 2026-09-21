"""Google Tasks provider. Read and write (completion only).

Satisfies TaskService structurally.

Note on nesting: Google Tasks allows exactly ONE level of subtasks, via a `parent`
field. That limitation is the provider's, not the domain's — TaskService models a
parent id, so a provider supporting deeper nesting would need no interface change.
See docs/integrations/README.md for the open question this settles.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx

from api.oauth import get_access_token
from api.providers.google_client import GoogleApiError, GoogleClient
from api.services import Task, TaskList

LISTS_URL = "https://tasks.googleapis.com/tasks/v1/users/@me/lists"
TASKS_URL = "https://tasks.googleapis.com/tasks/v1/lists/{list_id}/tasks"
TASK_URL = "https://tasks.googleapis.com/tasks/v1/lists/{list_id}/tasks/{task_id}"


class GoogleTasksProvider:
    source_id = "google_tasks"

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.client = GoogleClient(repo_root)

    def list_tasks(self, include_completed: bool = False) -> list[Task]:
        """Tasks across every list.

        Google scopes tasks to a list, and most people have more than one, so a
        single call would silently show only the default. Iterating all of them is
        the correct default even though it costs a request per list.

        provider_id is 'listId/taskId' because a task id alone is not addressable —
        every write needs the list too. Encoding it here keeps that Google detail
        out of the domain type.
        """
        tasks: list[Task] = []

        for task_list in self.client.paginate(LISTS_URL, limit=50):
            list_id = task_list["id"]

            for item in self.client.paginate(
                TASKS_URL.format(list_id=list_id),
                {
                    "showCompleted": "true" if include_completed else "false",
                    # Google keeps deleted and hidden tasks retrievable; neither
                    # should appear in a task list.
                    "showDeleted": "false",
                    "showHidden": "false",
                    "maxResults": 100,
                },
                limit=500,
            ):
                tasks.append(_to_task(item, list_id, task_list.get("title")))

        return tasks

    def list_task_lists(self) -> list[TaskList]:
        """Every list, including empty ones.

        Separate from list_tasks because a list's existence cannot be derived from
        its tasks when it has none — and that is exactly when it matters, since a
        new "To Read" list is empty at the moment you first want to add to it.
        """
        return [
            TaskList(provider_id=item["id"], name=item.get("title") or "(untitled)")
            for item in self.client.paginate(LISTS_URL, limit=50)
        ]

    def create_task_list(self, name: str) -> TaskList:
        """Make a new list.

        Closes the last gap in owning tasks from here: you could read lists,
        read and complete tasks, and add to a list — but a list that did not
        exist yet had to be created in Google Tasks itself, which is exactly
        the case that comes up (a new "To Read" is empty when you first want it).

        Google permits duplicate titles, and this does not prevent them: two
        lists called "To Read" is a mess the user made deliberately, and silently
        refusing or merging would be the provider inventing policy.
        """
        response = httpx.post(
            LISTS_URL,
            headers={"Authorization": f"Bearer {get_access_token(self.repo_root)}"},
            json={"title": name.strip()},
            timeout=30,
        )

        if not response.is_success:
            raise GoogleApiError(
                f"Could not create list: HTTP {response.status_code} "
                f"{response.text[:200]}"
            )

        item = response.json()
        return TaskList(provider_id=item["id"], name=item.get("title") or name.strip())

    def create_task(
        self,
        list_id: str,
        title: str,
        notes: str | None = None,
        due: datetime | None = None,
    ) -> Task:
        """Add a task to a list.

        A POST rather than a GET, so it does not go through GoogleClient — same as
        complete_task. The token comes from the same place, and an expired one
        surfaces as NeedsReconnect for the route to turn into a reconnect prompt.

        Google stores `due` as RFC3339 but honours only the date part — a time of
        day is accepted and then ignored. Sending midnight UTC rather than the
        caller's clock time avoids a task silently landing on the wrong day for
        anyone east or west of it.
        """
        body: dict = {"title": title.strip()}
        if notes:
            body["notes"] = notes
        if due is not None:
            at = due if due.tzinfo else due.replace(tzinfo=timezone.utc)
            body["due"] = at.astimezone(timezone.utc).strftime("%Y-%m-%dT00:00:00.000Z")

        response = httpx.post(
            TASKS_URL.format(list_id=list_id),
            headers={"Authorization": f"Bearer {get_access_token(self.repo_root)}"},
            json=body,
            timeout=30,
        )

        if not response.is_success:
            raise GoogleApiError(
                f"Could not create task: HTTP {response.status_code} "
                f"{response.text[:200]}"
            )

        # The response carries the task but not its list, so the name is not
        # available here without a second call. The id is what writes need; the
        # name is cosmetic and the next list_tasks fills it in.
        return _to_task(response.json(), list_id, None)

    def complete_task(self, provider_id: str) -> Task:
        """Mark a task done.

        A PATCH rather than a GET, so it does not go through GoogleClient.get.
        The token still comes from the same place — an expired one here surfaces as
        NeedsReconnect, which the route turns into a reconnect prompt.
        """
        list_id, _, task_id = provider_id.partition("/")
        if not list_id or not task_id:
            raise GoogleApiError(f"Malformed task id: {provider_id!r}")

        response = httpx.patch(
            TASK_URL.format(list_id=list_id, task_id=task_id),
            headers={"Authorization": f"Bearer {get_access_token(self.repo_root)}"},
            json={"status": "completed"},
            timeout=30,
        )

        if not response.is_success:
            raise GoogleApiError(
                f"Could not complete task: HTTP {response.status_code} "
                f"{response.text[:200]}"
            )

        return _to_task(response.json(), list_id, None)


def _to_task(item: dict, list_id: str, list_name: str | None) -> Task:
    due = item.get("due")
    return Task(
        provider_id=f"{list_id}/{item['id']}",
        title=item.get("title") or "(untitled)",
        completed=item.get("status") == "completed",
        # Google returns due dates as RFC3339 with a Z suffix, which
        # fromisoformat handles only from Python 3.11 onward.
        due=datetime.fromisoformat(due) if due else None,
        notes=item.get("notes"),
        # Present only on subtasks. Prefixed with the list so it matches the
        # provider_id format above and can be resolved without extra context.
        parent_id=f"{list_id}/{item['parent']}" if item.get("parent") else None,
        # Carried as data, not as a fallback inside notes. It used to be written
        # into `notes` only when a task had none of its own, so any task with real
        # notes silently lost which list it came from — and "To Read" vs "Work" is
        # exactly the distinction that must survive.
        list_name=list_name,
        list_id=list_id,
    )
