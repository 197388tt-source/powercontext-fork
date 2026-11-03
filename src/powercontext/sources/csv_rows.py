# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Offline, lossless CSV row capture and standard text evidence."""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel

from powercontext.sources.definitions import SourceDefinition, SourceProjection
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class CSVDocumentInput(BaseModel):
    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def _validate_document_id(document_id: str) -> None:
    if not document_id or document_id != document_id.strip():
        message = "document_id must be nonempty and have no surrounding whitespace"
        raise ValueError(message)


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    _validate_document_id(document_id)
    if not primary_key_value.strip():
        message = "primary key value must not be blank"
        raise ValueError(message)
    identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > 256:
        message = "CSV row identity exceeds 256 characters"
        raise ValueError(message)
    return identity


def _validate_columns(columns: tuple[str, ...], primary_key: str) -> int:
    if not columns or any(not column.strip() for column in columns) or len(set(columns)) != len(columns):
        message = "CSV headers must be nonblank and unique"
        raise ValueError(message)
    if primary_key not in columns:
        message = "primary_key must exactly match a header"
        raise ValueError(message)
    return columns.index(primary_key)


def _validate_row(value: CSVRowInput) -> str:
    _validate_document_id(value.document_id)
    index = _validate_columns(value.columns, value.primary_key)
    if len(value.values) != len(value.columns):
        message = "CSV row width differs from header"
        raise ValueError(message)
    if value.values[index] != value.primary_key_value:
        message = "primary_key_value differs from the row field"
        raise ValueError(message)
    return csv_row_source_id(value.document_id, value.primary_key_value)


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    _validate_document_id(value.document_id)
    try:
        reader = csv.reader(io.StringIO(value.content.decode("utf-8-sig"), newline=""), strict=True)
        columns = tuple(next(reader, ()))
        index = _validate_columns(columns, value.primary_key)
        rows = []
        seen: set[str] = set()
        for fields in reader:
            if not fields:
                continue
            if len(fields) != len(columns):
                message = "CSV row width differs from header"
                raise ValueError(message)
            key = fields[index]
            csv_row_source_id(value.document_id, key)
            if key in seen:
                message = "duplicate primary key"
                raise ValueError(message)
            seen.add(key)
            rows.append(
                CSVRowInput(
                    document_id=value.document_id,
                    primary_key=value.primary_key,
                    primary_key_value=key,
                    columns=columns,
                    values=tuple(fields),
                )
            )
        return tuple(rows)
    except (UnicodeError, csv.Error) as error:
        message = "Invalid UTF-8 CSV document"
        raise ValueError(message) from error


class _CSVRowProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type="csv-row",
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


class _CSVRowDefinition:
    name = "csv-row"
    version = "1"
    input_class = CSVRowInput
    source_class = CSVRowSource
    projections: tuple[SourceProjection[CSVRowSource], ...] = (_CSVRowProjection(),)

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        identity = _validate_row(value)
        return CSVRowSource(
            name=identity, source_id=identity, materialization=SourceMaterialization.CAPTURED, **value.model_dump()
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput.model_validate(source.model_dump())


CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = _CSVRowDefinition()
