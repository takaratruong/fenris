#!/usr/bin/env python3
"""
json-cli: A CLI tool for reading, validating, transforming, and outputting JSON config files.

Features:
1. Read JSON config from file or stdin
2. Validate against a JSON schema
3. Transform values (jq-style path expressions, type coercion, defaults)
4. Output to stdout or file
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional
import re


# ============================================================================
# FEATURE 1: JSON Config Reader
# ============================================================================

def read_config(source: Optional[str] = None) -> dict:
    """Read JSON config from file path or stdin if source is '-' or None."""
    if source is None or source == '-':
        content = sys.stdin.read()
    else:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {source}")
        content = path.read_text()
    
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")


# ============================================================================
# FEATURE 2: Schema Validator
# ============================================================================

def validate_type(value: Any, expected_type: str, path: str) -> list[str]:
    """Validate a value against an expected type. Returns list of errors."""
    errors = []
    type_map = {
        'string': str,
        'number': (int, float),
        'integer': int,
        'boolean': bool,
        'array': list,
        'object': dict,
        'null': type(None),
    }
    
    if expected_type not in type_map:
        return errors  # Unknown type, skip validation
    
    expected = type_map[expected_type]
    if not isinstance(value, expected):
        # Special case: int is also a number
        if expected_type == 'number' and isinstance(value, (int, float)) and not isinstance(value, bool):
            return errors
        errors.append(f"At '{path}': expected {expected_type}, got {type(value).__name__}")
    
    return errors


def validate_schema(data: Any, schema: dict, path: str = '$') -> list[str]:
    """Validate data against a JSON schema. Returns list of validation errors."""
    errors = []
    
    # Type validation
    if 'type' in schema:
        schema_type = schema['type']
        if isinstance(schema_type, list):
            # Union type
            type_valid = any(
                len(validate_type(data, t, path)) == 0 for t in schema_type
            )
            if not type_valid:
                errors.append(f"At '{path}': value doesn't match any of types {schema_type}")
        else:
            errors.extend(validate_type(data, schema_type, path))
    
    # Enum validation
    if 'enum' in schema and data not in schema['enum']:
        errors.append(f"At '{path}': value must be one of {schema['enum']}")
    
    # Object properties
    if isinstance(data, dict) and 'properties' in schema:
        for prop, prop_schema in schema['properties'].items():
            if prop in data:
                errors.extend(validate_schema(data[prop], prop_schema, f"{path}.{prop}"))
        
        # Required fields
        required = schema.get('required', [])
        for req in required:
            if req not in data:
                errors.append(f"At '{path}': missing required property '{req}'")
    
    # Array items
    if isinstance(data, list) and 'items' in schema:
        for i, item in enumerate(data):
            errors.extend(validate_schema(item, schema['items'], f"{path}[{i}]"))
    
    # Min/max for numbers
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if 'minimum' in schema and data < schema['minimum']:
            errors.append(f"At '{path}': value {data} is less than minimum {schema['minimum']}")
        if 'maximum' in schema and data > schema['maximum']:
            errors.append(f"At '{path}': value {data} is greater than maximum {schema['maximum']}")
    
    # String patterns
    if isinstance(data, str):
        if 'minLength' in schema and len(data) < schema['minLength']:
            errors.append(f"At '{path}': string length {len(data)} is less than minLength {schema['minLength']}")
        if 'maxLength' in schema and len(data) > schema['maxLength']:
            errors.append(f"At '{path}': string length {len(data)} is greater than maxLength {schema['maxLength']}")
        if 'pattern' in schema and not re.match(schema['pattern'], data):
            errors.append(f"At '{path}': string doesn't match pattern '{schema['pattern']}'")
    
    return errors


def load_and_validate(data: dict, schema_path: str) -> tuple[bool, list[str]]:
    """Load schema from file and validate data against it."""
    schema = json.loads(Path(schema_path).read_text())
    errors = validate_schema(data, schema)
    return len(errors) == 0, errors


# ============================================================================
# FEATURE 3: Value Transformer
# ============================================================================

def get_path(data: Any, path: str) -> Any:
    """Get value at a dot-notation path (e.g., 'server.host' or 'items[0].name')."""
    if not path or path == '.':
        return data
    
    current = data
    # Parse path segments: handle both dots and array indices
    segments = re.split(r'\.|\[(\d+)\]', path)
    segments = [s for s in segments if s is not None and s != '']
    
    for segment in segments:
        if isinstance(current, dict):
            if segment not in current:
                raise KeyError(f"Path '{path}' not found: missing key '{segment}'")
            current = current[segment]
        elif isinstance(current, list):
            try:
                idx = int(segment)
                current = current[idx]
            except (ValueError, IndexError) as e:
                raise KeyError(f"Path '{path}' not found: {e}")
        else:
            raise KeyError(f"Path '{path}' not found: cannot traverse {type(current).__name__}")
    
    return current


def set_path(data: dict, path: str, value: Any) -> dict:
    """Set value at a dot-notation path, creating intermediate objects as needed."""
    if not path or path == '.':
        return value
    
    segments = re.split(r'\.|\[(\d+)\]', path)
    segments = [s for s in segments if s is not None and s != '']
    
    current = data
    for i, segment in enumerate(segments[:-1]):
        next_segment = segments[i + 1]
        is_next_array = next_segment.isdigit()
        
        if isinstance(current, dict):
            if segment not in current:
                current[segment] = [] if is_next_array else {}
            current = current[segment]
        elif isinstance(current, list):
            idx = int(segment)
            while len(current) <= idx:
                current.append({} if not is_next_array else [])
            current = current[idx]
    
    # Set final value
    final_segment = segments[-1]
    if isinstance(current, dict):
        current[final_segment] = value
    elif isinstance(current, list):
        idx = int(final_segment)
        while len(current) <= idx:
            current.append(None)
        current[idx] = value
    
    return data


def apply_transforms(data: dict, transforms: list[str]) -> dict:
    """
    Apply a list of transformations to the data.
    
    Transform syntax:
    - 'path=value'      : Set path to literal value (auto-detect type)
    - 'path:=value'     : Set path to JSON-parsed value
    - 'path|upper'      : Apply string transformation
    - 'path|default=x'  : Set default if path is missing/null
    - 'path|int'        : Type coercion
    """
    result = data.copy()
    
    for transform in transforms:
        # JSON assignment: path:=json_value
        if ':=' in transform:
            path, value = transform.split(':=', 1)
            parsed_value = json.loads(value)
            result = set_path(result, path.strip(), parsed_value)
        
        # Pipe transforms: path|transform
        elif '|' in transform:
            path, op = transform.split('|', 1)
            path = path.strip()
            
            try:
                current_value = get_path(result, path)
            except KeyError:
                current_value = None
            
            # Default value
            if op.startswith('default='):
                if current_value is None:
                    default = op[8:]  # After 'default='
                    # Try to parse as JSON, fall back to string
                    try:
                        default = json.loads(default)
                    except json.JSONDecodeError:
                        pass
                    result = set_path(result, path, default)
            
            # String transforms
            elif op == 'upper' and isinstance(current_value, str):
                result = set_path(result, path, current_value.upper())
            elif op == 'lower' and isinstance(current_value, str):
                result = set_path(result, path, current_value.lower())
            elif op == 'trim' and isinstance(current_value, str):
                result = set_path(result, path, current_value.strip())
            
            # Type coercion
            elif op == 'int':
                result = set_path(result, path, int(current_value))
            elif op == 'float':
                result = set_path(result, path, float(current_value))
            elif op == 'str':
                result = set_path(result, path, str(current_value))
            elif op == 'bool':
                result = set_path(result, path, bool(current_value))
        
        # Simple assignment: path=value
        elif '=' in transform:
            path, value = transform.split('=', 1)
            path = path.strip()
            # Auto-detect type
            if value.lower() == 'true':
                parsed = True
            elif value.lower() == 'false':
                parsed = False
            elif value.lower() == 'null':
                parsed = None
            else:
                try:
                    parsed = int(value)
                except ValueError:
                    try:
                        parsed = float(value)
                    except ValueError:
                        parsed = value
            result = set_path(result, path, parsed)
    
    return result


# ============================================================================
# FEATURE 4: Output Handler
# ============================================================================

def output_result(data: Any, output_path: Optional[str] = None, 
                  pretty: bool = True, compact: bool = False) -> None:
    """Output JSON data to stdout or file."""
    if compact:
        content = json.dumps(data, separators=(',', ':'))
    elif pretty:
        content = json.dumps(data, indent=2)
    else:
        content = json.dumps(data)
    
    if output_path and output_path != '-':
        Path(output_path).write_text(content + '\n')
    else:
        print(content)


# ============================================================================
# CLI Entry Point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='JSON config CLI: read, validate, transform, and output JSON configs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Read and pretty-print
  json-cli config.json

  # Validate against schema
  json-cli config.json --schema schema.json

  # Transform values
  json-cli config.json --set 'server.port=8080' --set 'debug=true'

  # JSON value assignment
  json-cli config.json --set 'tags:=["a","b"]'

  # Apply defaults and coercion
  json-cli config.json --set 'timeout|default=30' --set 'port|int'

  # Output to file
  json-cli config.json -o output.json --compact
'''
    )
    
    parser.add_argument('input', nargs='?', default='-',
                        help='Input JSON file (default: stdin)')
    parser.add_argument('-s', '--schema', metavar='FILE',
                        help='JSON schema file for validation')
    parser.add_argument('--set', action='append', dest='transforms', metavar='EXPR',
                        help='Transform expression (repeatable)')
    parser.add_argument('-o', '--output', metavar='FILE',
                        help='Output file (default: stdout)')
    parser.add_argument('--pretty', action='store_true', default=True,
                        help='Pretty-print output (default)')
    parser.add_argument('--compact', action='store_true',
                        help='Compact output (no whitespace)')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='Suppress output, exit code only')
    parser.add_argument('--get', metavar='PATH',
                        help='Extract value at path and output it')
    
    args = parser.parse_args()
    
    try:
        # 1. Read config
        data = read_config(args.input if args.input != '-' else None)
        
        # 2. Validate if schema provided
        if args.schema:
            valid, errors = load_and_validate(data, args.schema)
            if not valid:
                for err in errors:
                    print(f"Validation error: {err}", file=sys.stderr)
                sys.exit(1)
        
        # 3. Apply transforms
        if args.transforms:
            data = apply_transforms(data, args.transforms)
        
        # 4. Extract path if --get specified
        if args.get:
            data = get_path(data, args.get)
        
        # 5. Output
        if not args.quiet:
            output_result(data, args.output, args.pretty, args.compact)
        
        sys.exit(0)
        
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(4)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
