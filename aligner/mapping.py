"""
Column mapping logic for DataFrame-to-SQL alignment.

Handles identifier normalization and intelligent column matching
between DataFrame columns and database table columns.
"""

import re
from typing import List, Dict, Tuple, Optional
from difflib import SequenceMatcher
from .models import ColumnMapping, TableSpec
from .type_system import TypeFamily, is_type_compatible


def normalize_identifier(name: str, dialect: str) -> str:
    """
    Normalize column/identifier name according to database dialect conventions.
    
    Args:
        name: Original identifier name
        dialect: Database dialect name
        
    Returns:
        Normalized identifier name
    """
    if not name:
        return name
    
    # Basic normalization
    normalized = name.strip()
    
    # Handle case sensitivity by dialect
    dialect = dialect.lower()
    
    if dialect == "oracle":
        # Oracle stores unquoted identifiers in uppercase
        if not normalized.startswith('"') and not normalized.endswith('"'):
            normalized = normalized.upper()
    elif dialect == "postgresql":
        # PostgreSQL stores unquoted identifiers in lowercase
        if not normalized.startswith('"') and not normalized.endswith('"'):
            normalized = normalized.lower()
    elif dialect == "mssql":
        # SQL Server is case-insensitive but preserves case
        if not normalized.startswith('[') and not normalized.endswith(']'):
            normalized = normalized.lower()
    elif dialect == "mysql":
        # MySQL is case-insensitive but preserves case
        if not normalized.startswith('`') and not normalized.endswith('`'):
            normalized = normalized.lower()
    elif dialect == "sqlite":
        # SQLite is case-insensitive but preserves case
        if not normalized.startswith('"') and not normalized.endswith('"'):
            normalized = normalized.lower()
    else:
        # Default to lowercase
        normalized = normalized.lower()
    
    return normalized


def calculate_similarity(str1: str, str2: str) -> float:
    """
    Calculate similarity between two strings using multiple metrics.
    
    Args:
        str1: First string
        str2: Second string
        
    Returns:
        Similarity score between 0.0 and 1.0
    """
    # Exact match
    if str1 == str2:
        return 1.0
    
    # Case-insensitive match
    if str1.lower() == str2.lower():
        return 0.95
    
    # Sequence matcher similarity
    sequence_ratio = SequenceMatcher(None, str1.lower(), str2.lower()).ratio()
    
    # Common prefix/suffix bonus
    prefix_bonus = 0.0
    suffix_bonus = 0.0
    
    if str1.lower().startswith(str2.lower()) or str2.lower().startswith(str1.lower()):
        prefix_bonus = 0.2
    
    if str1.lower().endswith(str2.lower()) or str2.lower().endswith(str1.lower()):
        suffix_bonus = 0.2
    
    # Word overlap (for multi-word names)
    words1 = set(re.findall(r'[a-z0-9]+', str1.lower()))
    words2 = set(re.findall(r'[a-z0-9]+', str2.lower()))
    
    if words1 and words2:
        intersection = len(words1.intersection(words2))
        union = len(words1.union(words2))
        word_overlap = intersection / union if union > 0 else 0.0
    else:
        word_overlap = 0.0
    
    # Weighted combination
    final_score = (
        sequence_ratio * 0.6 +
        word_overlap * 0.3 +
        prefix_bonus * 0.05 +
        suffix_bonus * 0.05
    )
    
    return min(final_score, 1.0)


def find_common_patterns(df_col: str, table_col: str) -> List[str]:
    """
    Find common naming patterns between DataFrame and table columns.
    
    Args:
        df_col: DataFrame column name
        table_col: Table column name
        
    Returns:
        List of pattern descriptions
    """
    patterns = []
    
    df_lower = df_col.lower()
    table_lower = table_col.lower()
    
    # Common prefixes/suffixes
    common_prefixes = ['id_', 'is_', 'has_', 'can_', 'should_', 'will_', 'did_', 'do_']
    common_suffixes = ['_id', '_count', '_flag', '_status', '_date', '_time', '_num', '_val']
    
    for prefix in common_prefixes:
        if df_lower.startswith(prefix) and table_lower.startswith(prefix):
            patterns.append(f"common_prefix_{prefix}")
            break
    
    for suffix in common_suffixes:
        if df_lower.endswith(suffix) and table_lower.endswith(suffix):
            patterns.append(f"common_suffix_{suffix}")
            break
    
    # Snake case vs camel case conversion
    df_snake = re.sub(r'(?<!^)(?=[A-Z])', '_', df_col).lower()
    if df_snake == table_lower or table_lower == df_snake:
        patterns.append("snake_case_conversion")
    
    # Underscore vs space vs hyphen
    df_normalized = re.sub(r'[_\-\s]+', '_', df_lower).strip('_')
    table_normalized = re.sub(r'[_\-\s]+', '_', table_lower).strip('_')
    
    if df_normalized == table_normalized:
        patterns.append("separator_normalization")
    
    # Abbreviation patterns
    abbreviations = {
        'num': 'number',
        'cnt': 'count',
        'qty': 'quantity',
        'amt': 'amount',
        'dt': 'date',
        'tm': 'time',
        'id': 'identifier',
        'ref': 'reference',
        'val': 'value',
        'desc': 'description',
        'addr': 'address',
        'tel': 'telephone',
        'ph': 'phone',
        'url': 'url',
        'uuid': 'uuid',
    }
    
    for short, full in abbreviations.items():
        if short in df_lower and full in table_lower:
            patterns.append(f"abbreviation_{short}_to_{full}")
            break
        elif full in df_lower and short in table_lower:
            patterns.append(f"abbreviation_{full}_to_{short}")
            break
    
    return patterns


def build_column_mapping(
    df_columns: List[str],
    table_spec: TableSpec,
    dialect: str = "oracle",
    confidence_threshold: float = 0.6
) -> Tuple[List[ColumnMapping], List[str], List[str]]:
    """
    Build column mappings between DataFrame columns and table columns.
    
    Args:
        df_columns: List of DataFrame column names
        table_spec: Table specification with column definitions
        dialect: Database dialect for identifier normalization
        confidence_threshold: Minimum confidence score for automatic mapping
        
    Returns:
        Tuple of:
        - List of ColumnMapping objects
        - List of unmatched DataFrame columns
        - List of unmatched table columns
    """
    mappings = []
    table_columns = list(table_spec.columns.keys())
    
    # Normalize all column names
    normalized_df_cols = {
        col: normalize_identifier(col, dialect) 
        for col in df_columns
    }
    normalized_table_cols = {
        col: normalize_identifier(col, dialect) 
        for col in table_columns
    }
    
    # Build similarity matrix
    similarity_matrix = {}
    for df_col in df_columns:
        similarities = {}
        df_norm = normalized_df_cols[df_col]
        
        for table_col in table_columns:
            table_norm = normalized_table_cols[table_col]
            
            # Calculate base similarity
            similarity = calculate_similarity(df_norm, table_norm)
            
            # Type compatibility bonus
            table_col_spec = table_spec.get_column(table_col)
            if table_col_spec:
                # We don't have DataFrame type info here, but we can add
                # type compatibility bonus later when we have DataFrame info
                pass
            
            similarities[table_col] = similarity
        
        similarity_matrix[df_col] = similarities
    
    # Find best matches using greedy algorithm
    used_table_cols = set()
    used_df_cols = set()
    
    # Sort by highest similarity first
    all_pairs = []
    for df_col, similarities in similarity_matrix.items():
        for table_col, similarity in similarities.items():
            if similarity >= confidence_threshold:
                all_pairs.append((df_col, table_col, similarity))
    
    # Sort by similarity descending
    all_pairs.sort(key=lambda x: x[2], reverse=True)
    
    # Assign mappings greedily
    for df_col, table_col, similarity in all_pairs:
        if df_col not in used_df_cols and table_col not in used_table_cols:
            # Find patterns for this match
            patterns = find_common_patterns(df_col, table_col)
            
            # Create mapping
            mapping = ColumnMapping(
                df_column=df_col,
                table_column=table_col,
                confidence=similarity,
                reasons=[f"similarity_{similarity:.2f}"] + patterns
            )
            mappings.append(mapping)
            
            used_df_cols.add(df_col)
            used_table_cols.add(table_col)
    
    # Identify unmatched columns
    unmatched_df_cols = [col for col in df_columns if col not in used_df_cols]
    unmatched_table_cols = [col for col in table_columns if col not in used_table_cols]
    
    return mappings, unmatched_df_cols, unmatched_table_cols


def validate_mapping(
    mapping: ColumnMapping,
    df_dtype: str,
    table_spec: TableSpec,
    dialect: str
) -> Tuple[bool, List[str]]:
    """
    Validate a column mapping considering type compatibility.
    
    Args:
        mapping: Column mapping to validate
        df_dtype: DataFrame column dtype string
        table_spec: Table specification
        dialect: Database dialect
        
    Returns:
        Tuple of (is_valid, list_of_issues)
    """
    issues = []
    
    # Get table column spec
    table_col_spec = table_spec.get_column(mapping.table_column)
    if not table_col_spec:
        issues.append(f"Table column '{mapping.table_column}' not found")
        return False, issues
    
    # Check type compatibility
    # Convert DataFrame dtype to TypeFamily (simplified)
    df_type_family = infer_dtype_family(df_dtype)
    
    if not is_type_compatible(df_type_family, table_col_spec.type_family, dialect):
        issues.append(
            f"Type incompatibility: {df_dtype} ({df_type_family.value}) "
            f"-> {table_col_spec.sql_type} ({table_col_spec.type_family.value})"
        )
    
    # Check nullability
    if not table_col_spec.nullable:
        issues.append(f"Target column '{mapping.table_column}' is NOT NULL")
    
    # Check length constraints for string types
    if df_type_family == TypeFamily.STRING and table_col_spec.max_length:
        # This would require actual data inspection for full validation
        issues.append(f"String length constraint: max {table_col_spec.max_length}")
    
    is_valid = len(issues) == 0
    return is_valid, issues


def infer_dtype_family(dtype_str: str) -> TypeFamily:
    """
    Infer TypeFamily from pandas dtype string.
    
    Args:
        dtype_str: Pandas dtype string
        
    Returns:
        TypeFamily: Inferred type family
    """
    dtype_lower = dtype_str.lower()
    
    if 'int' in dtype_lower:
        return TypeFamily.INTEGER
    elif 'float' in dtype_lower:
        return TypeFamily.FLOAT
    elif 'decimal' in dtype_lower:
        return TypeFamily.DECIMAL
    elif 'bool' in dtype_lower:
        return TypeFamily.BOOLEAN
    elif 'datetime' in dtype_lower or 'timestamp' in dtype_lower:
        return TypeFamily.DATETIME
    elif 'date' in dtype_lower and 'datetime' not in dtype_lower:
        return TypeFamily.DATE
    elif 'time' in dtype_lower and 'datetime' not in dtype_lower:
        return TypeFamily.TIME
    elif 'object' in dtype_lower:
        return TypeFamily.STRING  # Default for object dtype
    else:
        return TypeFamily.UNKNOWN


def suggest_column_renames(
    unmatched_df_cols: List[str],
    unmatched_table_cols: List[str],
    dialect: str
) -> List[Tuple[str, str, float]]:
    """
    Suggest column renames for unmatched columns.
    
    Args:
        unmatched_df_cols: Unmatched DataFrame column names
        unmatched_table_cols: Unmatched table column names
        dialect: Database dialect
        
    Returns:
        List of (df_col, table_col, confidence) tuples
    """
    suggestions = []
    
    for df_col in unmatched_df_cols:
        df_norm = normalize_identifier(df_col, dialect)
        
        for table_col in unmatched_table_cols:
            table_norm = normalize_identifier(table_col, dialect)
            similarity = calculate_similarity(df_norm, table_norm)
            
            if similarity >= 0.5:  # Lower threshold for suggestions
                suggestions.append((df_col, table_col, similarity))
    
    # Sort by confidence descending
    suggestions.sort(key=lambda x: x[2], reverse=True)
    
    return suggestions


def build_mapping_report(
    mappings: List[ColumnMapping],
    unmatched_df_cols: List[str],
    unmatched_table_cols: List[str],
    confidence_threshold: float = 0.8
) -> Dict[str, any]:
    """
    Build a comprehensive mapping report.
    
    Args:
        mappings: List of column mappings
        unmatched_df_cols: Unmatched DataFrame columns
        unmatched_table_cols: Unmatched table columns
        confidence_threshold: Threshold for high confidence
        
    Returns:
        Mapping report dictionary
    """
    high_confidence = [m for m in mappings if m.confidence >= confidence_threshold]
    medium_confidence = [m for m in mappings if confidence_threshold * 0.7 <= m.confidence < confidence_threshold]
    low_confidence = [m for m in mappings if m.confidence < confidence_threshold * 0.7]
    
    return {
        'total_mappings': len(mappings),
        'high_confidence_mappings': len(high_confidence),
        'medium_confidence_mappings': len(medium_confidence),
        'low_confidence_mappings': len(low_confidence),
        'unmatched_df_columns': len(unmatched_df_cols),
        'unmatched_table_columns': len(unmatched_table_cols),
        'average_confidence': sum(m.confidence for m in mappings) / len(mappings) if mappings else 0.0,
        'mappings_by_confidence': {
            'high': high_confidence,
            'medium': medium_confidence,
            'low': low_confidence
        },
        'unmatched_columns': {
            'dataframe': unmatched_df_cols,
            'table': unmatched_table_cols
        }
    }
