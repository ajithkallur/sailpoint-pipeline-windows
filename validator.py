
"""
SailPoint ISC Configuration Validator

"""

import json
from datetime import datetime

class SailPointValidator:
    """Basic validator for POC purposes."""
    
    def __init__(self, config_file: str):
        self.config_file = config_file
        self.data = None
        self.objects = []
        self.errors = []
    
    def validate(self) -> bool:
        """Run basic validation."""
        print("\n" + "="*60)
        print("🔍 SAILPOINT ISC CONFIGURATION VALIDATION")
        print("="*60)
        print(f"File: {self.config_file}")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Load file
        if not self._load_config():
            return self._print_results()
        
        # Validate structure
        self._validate_structure()
        
        return self._print_results()
    
    def _load_config(self) -> bool:
        """Load and parse config file."""
        print(f"\n📂 Loading configuration file...")
        
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
            print(f"   ✅ File loaded successfully")
            return True
        except FileNotFoundError:
            self.errors.append(f"File not found: {self.config_file}")
            return False
        except json.JSONDecodeError as e:
            self.errors.append(f"Invalid JSON: {e}")
            return False
    
    def _validate_structure(self):
        """Validate basic structure."""
        print("\n🔧 Validating basic structure...")
        
        # Check for objects array
        if "objects" not in self.data:
            self.errors.append("Missing 'objects' array")
            return
        
        self.objects = self.data["objects"]
        
        if not isinstance(self.objects, list):
            self.errors.append("'objects' must be an array")
            return
        
        if len(self.objects) == 0:
            self.errors.append("Export contains 0 objects")
            return
        
        print(f"   Found {len(self.objects)} objects")
        
        # Validate each object has minimum required fields
        for i, obj in enumerate(self.objects):
            if "self" not in obj:
                self.errors.append(f"Object[{i}]: Missing 'self' section")
            elif "type" not in obj.get("self", {}):
                self.errors.append(f"Object[{i}]: Missing 'self.type'")
            
            if "object" not in obj:
                self.errors.append(f"Object[{i}]: Missing 'object' section")
        
        print(f"   ✅ Structure validation complete")
    
    def _print_results(self) -> bool:
        """Print results."""
        print(f"\n📊 Validation Summary:")
        print(f"   Total objects: {len(self.objects)}")
        print(f"   Errors: {len(self.errors)}")
        
        if self.errors:
            print("\n❌ ERRORS:")
            for i, err in enumerate(self.errors, 1):
                print(f"   {i}. {err}")
        else:
            print("\n✅ All validations passed!")
        
        print("\n" + "="*60 + "\n")
        
        return len(self.errors) == 0


def validate_config_file(config_file: str) -> bool:
    """Convenience function."""
    validator = SailPointValidator(config_file)
    return validator.validate()
