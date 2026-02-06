import math

def calculate_distributed_storage(
    population=3315000, 
    days_buffer=90, 
    unit_length=20, 
    unit_width=8, 
    unit_height=8.5,
    packing_efficiency=0.75
):
    # Constants
    ANNUAL_CONS_PER_PERSON = 2000  # lbs
    COLD_RATIO = 0.45             # 45% of food needs refrigeration
    FOOD_DENSITY = 30             # lbs per cubic foot
    WAREHOUSE_EFFICIENCY = 0.40   # Basic overhead for large scale
    
    # 1. Total Volume needed (from previous model logic)
    total_lbs_year = population * ANNUAL_CONS_PER_PERSON * COLD_RATIO
    buffer_ratio = days_buffer / 365
    lbs_to_store = total_lbs_year * buffer_ratio
    
    # Required cubic feet of actual food
    required_food_volume = lbs_to_store / FOOD_DENSITY
    
    # Total "Gross Warehouse Volume" needed if using traditional buildings
    total_v_gross = required_food_volume / WAREHOUSE_EFFICIENCY
    
    # 2. Individual Unit Calculation
    v_unit_external = unit_length * unit_width * unit_height
    v_unit_usable = v_unit_external * packing_efficiency
    
    # 3. Number of Units
    # Note: We use the required_food_volume because containers replace 
    # the need for the 0.40 warehouse efficiency factor with their own 0.75 factor
    num_units = math.ceil(required_food_volume / v_unit_usable)
    
    return {
        "total_volume_needed_ft3": total_v_gross,
        "single_unit_usable_ft3": v_unit_usable,
        "units_required": num_units,
        "units_per_1000_people": (num_units / population) * 1000
    }

# Example 1: Using 20ft Refrigerated Containers (Reefers)
reefer_20 = calculate_distributed_storage(unit_length=20)

# Example 2: Using 40ft High-Cube Reefers
reefer_40 = calculate_distributed_storage(unit_length=40, unit_height=9.5)

print(f"For a 90-day reserve in the MN Metro:")
print(f"- Using 20ft Units: {reefer_20['units_required']:,} units (~{reefer_20['units_per_1000_people']:.2f} per 1,000 people)")
print(f"- Using 40ft Units: {reefer_40['units_required']:,} units (~{reefer_40['units_per_1000_people']:.2f} per 1,000 people)")