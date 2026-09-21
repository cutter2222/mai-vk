"use client";

import { Select, TextInput, Title } from "@mantine/core";
import { IconSearch } from "@tabler/icons-react";

interface Props {
  title: string;
  searchLabel: string;
  searchPlaceholder: string;
  query: string;
  onQueryChange: (value: string) => void;
  filterLabel: string;
  filter: string;
  onFilterChange: (value: string) => void;
  options: { value: string; label: string }[];
}

/** Общая компактная шапка каталогов презентаций и шаблонов. */
export function CatalogHeader({ title, searchLabel, searchPlaceholder, query, onQueryChange, filterLabel, filter, onFilterChange, options }: Props) {
  return (
    <div className="catalog-header" data-testid="catalog-header">
      <Title order={1} style={{ letterSpacing: "-0.03em" }}>{title}</Title>
      <div className="catalog-header-controls">
        <TextInput aria-label={searchLabel} placeholder={searchPlaceholder} leftSection={<IconSearch size={16} />} value={query} onChange={(e) => onQueryChange(e.currentTarget.value)} />
        <Select aria-label={filterLabel} value={filter} onChange={(value) => onFilterChange(value ?? "all")} allowDeselect={false} data={options} />
      </div>
    </div>
  );
}