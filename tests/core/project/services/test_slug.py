"""Unit tests for dovo.core.project.services.slug."""

import re

from dovo.core.project.models import PROJECT_ID_REGEX
from dovo.core.project.services.slug import ADJECTIVES, NOUNS, generate_slug


class ProjectSlugServiceTests:
    """Unit tests for random project slug generation."""

    def test_generate_slug_returns_valid_two_word_identifier_across_one_hundred_calls(self) -> None:
        """Each generated slug uses listed lowercase words and matches the project ID format."""
        generated_slugs = tuple(generate_slug() for _ in range(100))

        assert all(re.fullmatch(PROJECT_ID_REGEX, slug) for slug in generated_slugs)
        assert all(
            adjective in ADJECTIVES and noun in NOUNS
            for adjective, noun in (slug.split("-") for slug in generated_slugs)
        )
