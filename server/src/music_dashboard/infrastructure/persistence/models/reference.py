from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Timezone(Base):
    """Controlled IANA names. Valid iff present; canonical iff name = canonical_name."""

    __tablename__ = "timezones"
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(ForeignKey("timezones.name"))
