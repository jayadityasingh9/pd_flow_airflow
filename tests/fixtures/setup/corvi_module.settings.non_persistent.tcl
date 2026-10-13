puts "RM-info: Running script [info script]\n"

##########################################################################################
# Script: settings.non_persistent.tcl
# Description : Settings that need to be re-applied in each new ICC-II session are incldued below.
# Version: O-2018.06 (July 16, 2018)
# Copyright (C) 2014-2018 Synopsys, Inc. All rights reserved.
##########################################################################################

####################################
## Timer 
####################################
## set_app_options -name time.enable_preset_clear_arcs -value true ;# tool default false, global-scoped
# Creating separate ICG and async_default path groups in ICC2, similar to DC and PT
set_app_options -name time.use_special_default_path_groups -value true ;# tool default false


####################################
## Keepout margin (lib cell based) 
####################################
## Lib cell based keepout margin is not persistent in the current release and must be re-applied in new ICC-II seccions.
#  Example : create_keepout_margin -outer {5 5 5 5} [get_lib_cells */lib_cell_name]

####################################
## set_threshold_voltage_group_type 
####################################
## Set your threshold_voltage_group attributes. These are persistent and can be simply defined in settings.place_opt.tcl. 
#  Listed here for your reference. For example:
#  	define_user_attribute -type string -class lib_cell threshold_voltage_group
#  	set_attribute -quiet [get_lib_cells -quiet */*LVT] threshold_voltage_group LVt
#  	set_attribute -quiet [get_lib_cells -quiet */*RVT] threshold_voltage_group RVt
#  	set_attribute -quiet [get_lib_cells -quiet */*HVT] threshold_voltage_group HVt

## set_threshold_voltage_group_type is not persistent and should be defined here:
#  	set_threshold_voltage_group_type -type low_vt LVt
#  	set_threshold_voltage_group_type -type normal_vt RVt
#  	set_threshold_voltage_group_type -type high_vt HVt

# Setting max lvth percentage to 10% ULVT
#set_threshold_voltage_group_type -type low_vt ULVT08
# Command deprecated, replace with set_multi_Vth_constraint instead
# set_max_lvth_percentage 10
#set_attribute [get_lib_cells *ULT08*] threshold_voltage_group ULVT
#set_threshold_voltage_group_type -type low_vt ULVT
#set_multi_vth_constraint -low_vt_percentage 8

foreach vth [array names vth_group_override_patterns] {
        foreach { vt_type pattern } $vth_group_override_patterns($vth) {
            set lc [get_lib_cells -quiet $pattern]
            if { [sizeof_collection $lc] != 0 } {
                puts "[sizeof_collection $lc] cells are marked for $vth threshold_voltage_group"
                set_attribute $lc -name threshold_voltage_group -value $vth
                if { $vt_type != {} } {
                    set_threshold_voltage_group_type -type $vt_type $vth
                }
            }
        }
}
set_multi_vth_constraint -low_vt_percentage 10

# Setting time.enable_ccs_rcv_cap to true to help use advanced receiver model for delay calculation
set_app_options -name time.enable_ccs_rcv_cap -value true

# To use extended statistical moments from LVF libs for POCV delay calculation
set_app_options -name time.pocvm_enable_extended_moments -value true

# To ensure pocvm derate values are picked up from library and not from file
set_app_options -name time.pocvm_precedence  -value library

# Disable Auto Exception creation, so manual skewing works if needed
set_app_options -name cts.common.enable_auto_exceptions -value false

set_app_options -name opt.common.max_fanout -value 32
set_app_options -name cts.common.max_fanout -value 32
set_app_options -name opt.buffering.two_pass_hfs -value true

# Allow even spreading of cells
set_app_options -name place.coarse.congestion_driven_max_util -value 0.85
set_app_options -name place.coarse.auto_density_control -value false
set_app_options -name place.coarse.max_density -value 0.70
set_app_options -name compile.flow.high_effort_timing -value 1

# Exclude IO path groups from CCD
set_app_options -name ccd.skip_path_groups -value "**reg2out_default** **in2reg_default** **in2out_default**"

# To exclude the vt_min_area legality failures
# set_app_options -name place.legalize.check_vt_min_area_for_multi_height_cells -value false

# Limit skewing when CCD is on
set_app_options -list {ccd.max_prepone 0.035}
set_app_options -list {ccd.max_postpone 0.035}

puts "RM-info: Completed script [info script]\n"
