##########################################################################################
# Tool: Fusion Compiler
# Script: compile.tcl
# Version: R-2020.09-SP6
# Copyright (C) 2014-2021 Synopsys, Inc. All rights reserved.
##########################################################################################
source ../../scripts_block/rm_utilities/procs_global.tcl
source -echo ../../scripts_block/rm_setup/fc_setup.tcl
source -echo ../../block_files/$env(TECH_DIR)/sidefile_setup.tcl 
source -echo -verbose ../../scripts_block/rm_utilities/procs_fc.tcl

set REPORT_PREFIX ${COMPILE_BLOCK_NAME}
set REPORTS_DIR $REPORTS_DIR/$REPORT_PREFIX; file mkdir $REPORTS_DIR
set_svf ${OUTPUTS_DIR}/${COMPILE_BLOCK_NAME}.svf

open_lib ${DESIGN_LIBRARY}
copy_block -from ${DESIGN_NAME}/${INIT_DESIGN_BLOCK_NAME} -to ${DESIGN_NAME}/${COMPILE_BLOCK_NAME}
current_block ${DESIGN_NAME}/${COMPILE_BLOCK_NAME}

if {$EARLY_DATA_CHECK_POLICY != "none"} {
	## Design check manager
	set_early_data_check_policy -policy $EARLY_DATA_CHECK_POLICY -if_not_exist
}

link_block
#set_app_options -name opt.common.preserve_pin_names -value always
set_disable_timing -from SD -to RD [get_lib_cells */*FSDPRBSBQ*]
set_disable_timing -from RD -to SD [get_lib_cells */*FSDPRBSBQ*]
## For top and intermediate level of hierarchical designs, check the editability of the sub-blocks;
### if the editability is true for any sub-block, set it to false
### In RM, we are setting the editability of all the sub-blocks to false in the init_design.tcl script
### The following check is implemented to ensure that the editability of the sub-blocks is set to false in flows not running the init_design.tcl script
if {$USE_ABSTRACTS_FOR_BLOCKS != ""} {
        foreach_in_collection c [get_blocks -hierarchical] {
                if {[get_editability -blocks ${c}] == true } {
                set_editability -blocks ${c} -value false
                }
        }
        report_editability -blocks [get_blocks -hierarchical]
}

## Set active scenarios for the step (please include CTS and hold scenarios for CCD) ;
if {$COMPILE_ACTIVE_SCENARIO_LIST != ""} {
	set_scenario_status -active false [get_scenarios -filter active]
	set_scenario_status -active true $COMPILE_ACTIVE_SCENARIO_LIST
}

if {[sizeof_collection [get_scenarios -filter "hold && active"]] == 0} {
	puts "RM-warning: No active hold scenario is found. Recommended to enable hold scenarios here such that CCD skewing can consider them." 
	puts "RM-info: Please activate hold scenarios for compile_fusion if they are available." 
}


#source -echo place_opt.tcl.7nm_t.common ;# node/foundry specific (common)
#source -echo place_opt.tcl.sg_7nm_t ;# node/foundry specific (h240 library)

## Non-persistent settings to be applied in each step (optional)
if {[file exists [which $TCL_USER_NON_PERSISTENT_SCRIPT]]} {
	puts "RM-info: Sourcing [which $TCL_USER_NON_PERSISTENT_SCRIPT]"
	source -echo $TCL_USER_NON_PERSISTENT_SCRIPT
} elseif {$TCL_USER_NON_PERSISTENT_SCRIPT != ""} {
	puts "RM-error: TCL_USER_NON_PERSISTENT_SCRIPT($TCL_USER_NON_PERSISTENT_SCRIPT) is invalid. Please correct it."
}

##########################################################################################
## Settings
##########################################################################################

## set_qor_strategy : a commmand which folds various settings of placement, optimization, timing, CTS, and routing, etc.
## - To query the target metric being set, use the "get_attribute [current_design] metric_target" command
set set_qor_strategy_cmd "set_qor_strategy -stage synthesis -metric \"${SET_QOR_STRATEGY_METRIC}\""

if {$ENABLE_REDUCED_EFFORT} {
   lappend set_qor_strategy_cmd -reduced_effort
   puts "RM-info: When reduced_effort is enabled, high effort timing is always disabled"
} elseif {(!$ENABLE_REDUCED_EFFORT && $ENABLE_HIGH_EFFORT_TIMING)} {
   lappend set_qor_strategy_cmd -high_effort_timing
}
puts "RM-info: Running $set_qor_strategy_cmd" 
eval ${set_qor_strategy_cmd}


## set_stage : a command to apply stage-based application options; intended to be used after set_qor_strategy within RM scripts.
set set_stage_cmd "set_stage -step synthesis"
puts "RM-info: Running ${set_stage_cmd}"
eval ${set_stage_cmd}


## Prefix
set_app_options -name opt.common.user_instance_name_prefix -value compile_
set_app_options -name cts.common.user_instance_name_prefix -value compile_cts_

## Routing 
## Set max routing layer
if {$MAX_ROUTING_LAYER != ""} {set_ignored_layers -max_routing_layer $MAX_ROUTING_LAYER}
## Set min routing layer
if {$MIN_ROUTING_LAYER != ""} {set_ignored_layers -min_routing_layer $MIN_ROUTING_LAYER}

## For set_qor_strategy -metric leakage_power, disabling the dynamic power analysis in active scenarios for optimization
# Scenario power analysis will be renabled after optimization for reporting
if {$SET_QOR_STRATEGY_METRIC == "leakage_power"} {
   set rm_dynamic_scenarios [get_object_name [get_scenarios -filter active==true&&dynamic_power==true]]

   if {[llength $rm_dynamic_scenarios] > 0} {
      puts "RM-info: Disabling dynamic analysis for $rm_dynamic_scenarios"
      set_scenario_status -dynamic_power false [get_scenarios $rm_dynamic_scenarios]
  }
}

####################################
## Routing
####################################
set_app_options -name route.common.derive_connect_within_pin_via_region -value true ;# Configure ICC2 to create appropriate via region based on pin width

### Check and remove duplicate PG shapes which casue issue with "compile_fusion -check_only"
check_duplicates
check_duplicates -remove
check_duplicates
check_pg_connectivity
check_pg_drc
##########################################################################################
## Additional setup
##########################################################################################
## Create clock NDR - specify TCL_CTS_NDR_RULE_FILE with your script to create and associate your clock NDR rules.
## RM default is cts_ndr.tcl which is an RM provided example. Refer to the script for setup and details.
## You need to also specify CTS_NDR_RULE_NAME, CTS_INTERNAL_NDR_RULE_NAME, or CTS_LEAF_NDR_RULE_NAME for it to take effect.
if {[file exists [which $TCL_CTS_NDR_RULE_FILE]]} {
	source -echo $TCL_CTS_NDR_RULE_FILE
} elseif {$TCL_CTS_NDR_RULE_FILE != ""} {
	puts "RM-error: TCL_CTS_NDR_RULE_FILE($TCL_CTS_NDR_RULE_FILE) is invalid. Please correct it!"
}

## Report NDR and clock settings
redirect -file ${REPORTS_DIR}/${REPORT_PREFIX}.report_routing_rules {report_routing_rules -verbose}
redirect -file ${REPORTS_DIR}/${REPORT_PREFIX}.report_clock_routing_rules {report_clock_routing_rules}
redirect -file ${REPORTS_DIR}/${REPORT_PREFIX}.report_clock_settings {report_clock_settings}

## CTS primary corner
## CTS automatically picks a corner with worst delay as the primary corner for its compile stage, 
## which inserts buffers to balance clock delays in all modes of the corner; 
## this setting allows you to manually specify a corner for the tool to use instead
if {$PREROUTE_CTS_PRIMARY_CORNER != ""} {
	puts "RM-info: Setting cts.compile.primary_corner to $PREROUTE_CTS_PRIMARY_CORNER (tool default unspecified)"
	set_app_options -name cts.compile.primary_corner -value $PREROUTE_CTS_PRIMARY_CORNER
}

### Restrict SCAN replacement
#set_app_options -name compile.seqmap.scan -value false
#set_scan_element false [get_designs]


## Lib cell usage restrictions (set_lib_cell_purpose)
## By default, RM sources set_lib_cell_purpose.tcl for dont use, tie cell, hold fixing, CTS and CTS-exclusive cell restrictions. 
## You can replace it with your own script by specifying the TCL_LIB_CELL_PURPOSE_FILE variable.  
if {[file exists [which $TCL_LIB_CELL_PURPOSE_FILE]]} {
	puts "RM-info: Sourcing [which $TCL_LIB_CELL_PURPOSE_FILE]"
	source $TCL_LIB_CELL_PURPOSE_FILE
} elseif {$TCL_LIB_CELL_PURPOSE_FILE != ""} {
	puts "RM-error: TCL_LIB_CELL_PURPOSE_FILE($TCL_LIB_CELL_PURPOSE_FILE) is invalid. Please correct it."
}

###########################################################################################
## read_saif 
###########################################################################################
if {[file exists [which $SAIF_FILE]]} {
	if {$SAIF_FILE_POWER_SCENARIO != ""} {
		set read_saif_cmd "read_saif $SAIF_FILE -scenarios $SAIF_FILE_POWER_SCENARIO"
	} else {
		set read_saif_cmd "read_saif $SAIF_FILE"
	}
	if {$SAIF_FILE_SOURCE_INSTANCE != ""} {lappend read_saif_cmd -strip_path $SAIF_FILE_SOURCE_INSTANCE}
	if {$SAIF_FILE_TARGET_INSTANCE != ""} {lappend read_saif_cmd -path $SAIF_FILE_TARGET_INSTANCE}
	puts "RM-info: Running $read_saif_cmd"
    	eval ${read_saif_cmd}
} elseif {$SAIF_FILE != ""} {
	puts "RM-error: SAIF_FILE($SAIF_FILE) is invalid. Please correct it."	
}

	#  Read the test model for the subblocks (used with "top" or "intermediate" hierarchical blocks)
	foreach ctl $CTL_FOR_ABSTRACT_BLOCKS {
		read_test_model $ctl
	}

## Via ladder
if {$ENABLE_PERFORMANCE_VIA_LADDER} {
	## (Optional) source user provided via ladder definitions, if not defined in your technology file
	if {[file exists [which $TCL_VIA_LADDER_DEFINITION_FILE]]} {
		puts "RM-info: Sourcing [which $TCL_VIA_LADDER_DEFINITION_FILE]"
		source $TCL_VIA_LADDER_DEFINITION_FILE
	} elseif {$TCL_VIA_LADDER_DEFINITION_FILE != ""} {
		puts "RM-error: TCL_VIA_LADDER_DEFINITION_FILE($TCL_VIA_LADDER_DEFINITION_FILE) is invalid. Please correct it."
	}
	
	## (Optional) source user provided library specific via ladder constraints
	## For ex, set_via_ladder_candidate [get_lib_pins */AIOI/ZN] -ladder_name "VP"
	## For ex, set_attribute -quiet [get_lib_pins */AIOI/ZN] is_em_via_ladder_required true
	if {[file exists [which $TCL_SET_VIA_LADDER_CANDIDATE_FILE]]} {
		puts "RM-info: Sourcing [which $TCL_SET_VIA_LADDER_CANDIDATE_FILE]"
		source $TCL_SET_VIA_LADDER_CANDIDATE_FILE
	} elseif {$TCL_SET_VIA_LADDER_CANDIDATE_FILE != ""} {
		puts "RM-error: TCL_SET_VIA_LADDER_CANDIDATE_FILE($TCL_SET_VIA_LADDER_CANDIDATE_FILE) is invalid. Please correct it."
	}
}

###########################################################################################
## Pre-compile customizations
###########################################################################################
if {[file exists [which $TCL_USER_COMPILE_PRE_SCRIPT]]} {
        puts "RM-info: Sourcing [which $TCL_USER_COMPILE_PRE_SCRIPT]"
        source -echo $TCL_USER_COMPILE_PRE_SCRIPT
} elseif {$TCL_USER_COMPILE_PRE_SCRIPT != ""} {
        puts "RM-error: TCL_USER_COMPILE_PRE_SCRIPT($TCL_USER_COMPILE_PRE_SCRIPT) is invalid. Please correct it."
}

##########################################################################################
## In-Compile DFT Insertion
##########################################################################################

  ##########################################################################################
  ## DFTMAX Compression Configuration (flat flows and bottom-up flows)
  ##########################################################################################

  # For flat flows, make compression specifications in your DFT setup file using the 
  # set_scan_compression_configuration command
  # Then run insert_dft command to insert DFTMAX codec structures before compile.

  # For bottom-up flows, we recommend you only insert scan chains at the block level,
  # and insert a DFTMAX codec at the top level.
  # Be sure to insert a large number of short scan chains at the block level.
  #
  # However if you choose to insert DFTMAX Compression at the block level, then
  # make compression specifications in your DFT setup file using the 
  # set_scan_compression_configuration command
  # and then use DFTMAX Hybrid flow at the top
  # level if you have any uncompressed blocks and want to insert DFTMAX codec
  # at the top level, or choose DFTMAX HASS Integration flow if you do not have
  # any uncompressed blocks and do not want to insert a DFTMAX codec at the top level.

  # For details on DFTMAX Hybrid or HASS Integration flows, please refer to the
  # TestMAX DFT User Guide, Chapter 19, "Hierarchical Adaptive Scan Synthesis"

puts "RM-info: Setting dft.insertion_post_logic_opto to true for in_compile DFT flow"
set_app_options -name dft.insertion_post_logic_opto -value true
## Specify set_scan_element false and set_wrapper_configuration -reuse_threshold commands 
#  prior to compile_fusion -to logic_opto command for an in_compile DFT flow
if {[file exists [which $TCL_DFT_PRE_IN_COMPILE_SETUP_FILE]]} {
   puts "RM-info: Sourcing [which $TCL_DFT_PRE_IN_COMPILE_SETUP_FILE]"
   source -echo $TCL_DFT_PRE_IN_COMPILE_SETUP_FILE
} elseif {$TCL_DFT_PRE_IN_COMPILE_SETUP_FILE != ""}  {
   puts "RM-error: TCL_DFT_PRE_IN_COMPILE_SETUP_FILE($TCL_DFT_PRE_IN_COMPILE_SETUP_FILE) is invalid. Please correct it."
}

##########################################################################################
## Create MV cells
##########################################################################################
# create_mv_cells is optional as MV cells are automatically inserted during compile
# puts "RM-info: Running create_mv_cells"
# create_mv_cells -verbose

check_duplicates > ${REPORTS_DIR}/check_duplicates.rpt

########################################################################################
# Allowing tool to pick scan flops
# #######################################################################################
set_attribute [get_lib_cells */*] is_lssd_cell false

########################################################################################
## Allowing no register placement blockage to be honored by Advanced legalizer
##########################################################################################
set_app_options -name place.legalize.enable_category_blockages -value true


##########################################################################################
## Checks
##########################################################################################
puts "RM-info: Running compile_fusion -check_only"
compile_fusion -check_only

redirect -tee -file ${REPORTS_DIR}/${REPORT_PREFIX}.report_app_options.start {report_app_options -non_default *}
redirect -file ${REPORTS_DIR}/${REPORT_PREFIX}.report_lib_cell_purpose {report_lib_cell -objects [get_lib_cells] -column {full_name:20 valid_purposes}}

set check_stage_settings_cmd "check_stage_settings -stage synthesis -metric \"${SET_QOR_STRATEGY_METRIC}\" -step synthesis"
if {$ENABLE_REDUCED_EFFORT} {
 lappend check_stage_settings_cmd -reduced_effort
} elseif {(!$ENABLE_REDUCED_EFFORT && $ENABLE_HIGH_EFFORT_TIMING)} {
 lappend check_stage_settings_cmd -high_effort_timing
}
if {$RESET_CHECK_STAGE_SETTINGS} {lappend check_stage_settings_cmd -reset_app_options}
redirect -file ${REPORTS_DIR}/${REPORT_PREFIX}.check_stage_settings {eval ${check_stage_settings_cmd}}

puts "RM-info: Marking clock network as ideal"
set currentMode [current_mode]
foreach_in_collection mode [all_modes] {
    current_mode $mode
    set clock_tree [all_fanout -flat -clock_tree]
    if { [sizeof_collection $clock_tree] > 0 } {
        set_ideal_network $clock_tree
        remove_propagated_clock [get_pins -hierarchical]
        remove_propagated_clock [get_ports]
        remove_propagated_clock [all_clocks]
    }
}
current_mode $currentMode

##########################################################################################
## Clock NDR modeling at compile_fusion
##########################################################################################
## Clock NDRs are created in settings.compile.tcl. 
## mark_clock_trees makes compile_fusion recognize them to model the congestion impact when trial CTS is not run.
puts "RM-info: Running mark_clock_trees -routing_rules to model clock NDR impact during compile_fusion"
mark_clock_trees -routing_rules

##########################################################################################
## Retiming
##########################################################################################
#set_optimize_registers -modules [get_module rvn_trg]

if {![info exists COMPILE_CCD]} {
# Disable CCD and Clock gate latency estimation
set_app_options -name compile.flow.enable_ccd -value false
set_app_options -name place_opt.flow.enable_ccd -value false
set_app_options -name opt.common.estimate_clock_gate_latency -value false
set_app_options -name place_opt.flow.estimate_clock_gate_latency -value false
}

check_legalizer_sanity > sanity_pre_logic_opto.rpt

##########################################################################################
## Initial Compile
##########################################################################################
set compile_cmd "compile_fusion -to logic_opto"
puts "RM-info: Running ${compile_cmd}"
eval ${compile_cmd}
save_block -as ${DESIGN_NAME}/compile_logic_opto
report_qor -summary
set_disable_timing -from SD -to RD [get_lib_cells */*FSDPRBSBQ*]
set_disable_timing -from RD -to SD [get_lib_cells */*FSDPRBSBQ*]
#remove_bounds -all

#########################################################################################
# Verify type of scan cells available
#########################################################################################
sizeof_collection [get_flat_cells -filter "is_test_cell==true"]

##########################################################################################
## Apply Mapped Netlist Constraints 
##########################################################################################
if {[file exists [which $TCL_USER_MAPPED_CONSTRAINTS_SCRIPT]]} {
        puts "RM-info: Sourcing [which $TCL_USER_MAPPED_CONSTRAINTS_SCRIPT]"
        source -echo $TCL_USER_MAPPED_CONSTRAINTS_SCRIPT
} elseif {$TCL_USER_MAPPED_CONSTRAINTS_SCRIPT != ""} {
        puts "RM-error: TCL_USER_MAPPED_CONSTRAINTS_SCRIPT($TCL_USER_MAPPED_CONSTRAINTS_SCRIPT) is invalid. Please correct it."
}

if {![info exists COMPILE_CCD]} {
# Disable CCD and Clock gate latency estimation
set_app_options -name compile.flow.enable_ccd -value false
set_app_options -name place_opt.flow.enable_ccd -value false
set_app_options -name opt.common.estimate_clock_gate_latency -value false
set_app_options -name place_opt.flow.estimate_clock_gate_latency -value false
}

check_legalizer_sanity > sanity_pre_initial_drc.rpt

set compile_cmd "compile_fusion -from initial_place -to initial_drc"
puts "RM-info: Running ${compile_cmd}"
eval ${compile_cmd}
report_qor -summary
save_block -as ${DESIGN_NAME}/compile_initial_drc

#remove_bounds -all

if {[file exists [which $TCL_USER_COMPILE_PRE_INITIAL_OPTO_SCRIPT]]} {
    puts "RM-info: Sourcing [which $TCL_USER_COMPILE_PRE_INITIAL_OPTO_SCRIPT]"
    source -echo $TCL_USER_COMPILE_PRE_INITIAL_OPTO_SCRIPT
} elseif {$TCL_USER_COMPILE_PRE_INITIAL_OPTO_SCRIPT != ""} {
    puts "RM-error: TCL_USER_COMPILE_PRE_INITIAL_OPTO_SCRIPT($TCL_USER_COMPILE_PRE_INITIAL_OPTO_SCRIPT) is invalid. Please correct it"
}


if {[file exists [which $TCL_DFT_SETUP_FILE]]} {
    puts "RM-info: Sourcing [which $TCL_DFT_SETUP_FILE]"
    source -echo $TCL_DFT_SETUP_FILE
    puts "RM-info: Running create_test_protocol"
    create_test_protocol    
    redirect -tee ${REPORTS_DIR}/${REPORT_PREFIX}.initial_opto.pre-insert_dft.dft_drc {dft_drc -verbose -test_mode all_dft}
    redirect -file ${REPORTS_DIR}/${REPORT_PREFIX}.pre-insert_dft.report_dft { report_dft }
    # In “in-compile” DFT insertion flow, insert_dft command inserts the DFTMAX Codec and performs scan stitching.
    # Use the preview_dft command to report on the DFTMAX Codec and scan chain structures prior to actual insertion

    redirect -file ${REPORTS_DIR}/${REPORT_PREFIX}.preview_dft_pre_dft { preview_dft }
    puts "RM-info: Running insert_dft"
    insert_dft   
    save_block -as ${DESIGN_NAME}/${INSERT_DFT_BLOCK_NAME}
} elseif {$TCL_DFT_SETUP_FILE != ""} {
    puts "RM-error: TCL_DFT_SETUP_FILE($TCL_DFT_SETUP_FILE) is invalid. Please correct it."
}

if {![info exists COMPILE_CCD]} {
# Disable CCD and Clock gate latency estimation
set_app_options -name compile.flow.enable_ccd -value false
set_app_options -name place_opt.flow.enable_ccd -value false
set_app_options -name opt.common.estimate_clock_gate_latency -value false
set_app_options -name place_opt.flow.estimate_clock_gate_latency -value false
}

check_legalizer_sanity > sanity_pre_initial_opto.rpt

set compile_cmd "compile_fusion -from initial_opto -to initial_opto"
puts "RM-info: Running ${compile_cmd}"
eval ${compile_cmd}
report_qor -summary
save_block -as ${DESIGN_NAME}/compile_initial_opto

## write test protocols for each DFT mode
foreach mode $DFT_MODES_LIST {
    redirect -tee $REPORTS_DIR/$COMPILE_BLOCK_NAME.initial_opto.$mode.dft_drc {dft_drc -test_mode $mode -coverage_estimate }
    write_test_protocol -test_mode $mode -output $OUTPUTS_DIR/$COMPILE_BLOCK_NAME.initial_opto.$mode.spf
}

foreach mode $DFT_MODES_LIST {
    if {[file exists [which $REPORTS_DIR/$COMPILE_BLOCK_NAME.initial_opto.$mode.dft_drc]]} {
        set err [catch {exec grep "Rule S19" $REPORTS_DIR/$COMPILE_BLOCK_NAME.initial_opto.$mode.dft_drc}]
        if {${err} == "0"} {
                echo "Rule S19 violation exists and hence exiting"
                dft_drc -test_mode $mode -verbose > $REPORTS_DIR/dft_drc_full.rpt
                exit
        }
    }
}

##########################################################################
## Regular MSCTS at place_opt 
##########################################################################
if {$CTS_STYLE == "MSCTS"} {
        if {[file exists [which $TCL_REGULAR_MSCTS_FILE]]} {
                set_app_options -name compile.flow.enable_multisource_clock_trees -value true
		puts "RM-info: Sourcing [which $TCL_REGULAR_MSCTS_FILE]"
               	source $TCL_REGULAR_MSCTS_FILE
                save_block -as ${DESIGN_NAME}/${COMPILE_BLOCK_NAME}_MSCTS

        } elseif {$TCL_REGULAR_MSCTS_FILE != ""} {
                puts "RM-error: TCL_REGULAR_MSCTS_FILE($TCL_REGULAR_MSCTS_FILE) is invalid. Please correct it."
        }

} elseif {$CTS_STYLE != "standard"} {
	puts "RM-error: Specified CTS_STYLE($CTS_STYLE) is not supported, standard will be used." 
}

#########################################################################################
## Reporting synthesis QOR
#########################################################################################
proc_qor 


##########################################################################################
## Unified Flow
##########################################################################################
if {${UNIFIED_FLOW}} {

        ## set_stage : a command to apply stage-based application options; intended to be used after set_qor_strategy within RM scripts.
        set set_stage_cmd "set_stage -step compile_place"
        puts "RM-info: Running ${set_stage_cmd}"
        eval ${set_stage_cmd}


        if {[file exists [which $TCL_USER_COMPILE_PRE_UNIFIED_SCRIPT]]} {
            puts "RM-info: Sourcing [which $TCL_USER_COMPILE_PRE_UNIFIED_SCRIPT]"
            source -echo $TCL_USER_COMPILE_PRE_UNIFIED_SCRIPT
        } elseif {$TCL_USER_COMPILE_PRE_UNIFIED_SCRIPT != ""} {
            puts "RM-error: TCL_USER_COMPILE_PRE_UNIFIED_SCRIPT($TCL_USER_COMPILE_PRE_UNIFIED_SCRIPT) is invalid. Please correct it"
        }

  	if {![info exists COMPILE_CCD]} {
	# Disable CCD and Clock gate latency estimation
	set_app_options -name compile.flow.enable_ccd -value false
	set_app_options -name place_opt.flow.enable_ccd -value false
	set_app_options -name opt.common.estimate_clock_gate_latency -value false
	set_app_options -name place_opt.flow.estimate_clock_gate_latency -value false
	}

	if {![info exists POCV]} {
	set_app_options -name time.pocvm_enable_analysis -value false
	}

        set compile_cmd "compile_fusion -from final_place"
        puts "RM-info: Running First final_place ${compile_cmd}"
        eval ${compile_cmd}
        report_qor -summary
	proc_qor
	# Save intermediate block
	save_block -as ${DESIGN_NAME}/compile_final_place1
	
}

###########################################################################################
## Post-compile customizations
###########################################################################################
if {[file exists [which $TCL_USER_COMPILE_POST_SCRIPT]]} {
        puts "RM-info: Sourcing [which $TCL_USER_COMPILE_POST_SCRIPT]"
        source -echo $TCL_USER_COMPILE_POST_SCRIPT
} elseif {$TCL_USER_COMPILE_POST_SCRIPT != ""} {
        puts "RM-error: TCL_USER_COMPILE_POST_SCRIPT($TCL_USER_COMPILE_POST_SCRIPT) is invalid. Please correct it."
}

if {$TCL_USER_CONNECT_PG_NET_SCRIPT != ""} {
        if {[file exists [which $TCL_USER_CONNECT_PG_NET_SCRIPT]]} {
               puts "RM-info: Sourcing [which $TCL_USER_CONNECT_PG_NET_SCRIPT]"
               source $TCL_USER_CONNECT_PG_NET_SCRIPT
        } else {
               puts "RM-error: TCL_USER_CONNECT_PG_NET_SCRIPT($TCL_USER_CONNECT_PG_NET_SCRIPT) is invalid. Please correct it."
        }
} else {
        connect_pg_net
        # For non-MV designs with more than one PG, you should use connect_pg_net in manual mode.
}
################remove the routing blocakges of M2 layer near macros
remove_routing_blockages -all

## Re-enable power analysis if disabled for set_qor_strategy -metric leakage_power
if {[info exists rm_dynamic_scenarios] && [llength $rm_dynamic_scenarios] > 0} {
   puts "RM-info: Reenabling dynamic power analysis for $rm_dynamic_scenarios"
   set_scenario_status -dynamic_power true [get_scenarios $rm_dynamic_scenarios]
}

## quasi-verilog rules set to preserve busnames for ports
if {[file exists [which quasi_verilog_change_names.tcl]]} {
        puts "RM-info: Sourcing [which quasi_verilog_change_names.tcl]"
        source -echo quasi_verilog_change_names.tcl
	change_names -rules quasi-verilog -hierarchy -skip_physical_only_cells
} else {
	change_names -rules verilog -hierarchy -skip_physical_only_cells
}

if {$UPF_MODE == "golden"} {
        set upf_files "${UPF_FILE}"
        if {[file exists [which ${UPF_UPDATE_SUPPLY_SET_FILE}]]} { lappend upf_files "${UPF_UPDATE_SUPPLY_SET_FILE}" }                        
        write_ascii_files -force \
            -output ${OUTPUTS_DIR}/${COMPILE_BLOCK_NAME}.ascii_files \
            -golden_upf "${upf_files}"
} else {
        write_ascii_files -force \
            -output ${OUTPUTS_DIR}/${COMPILE_BLOCK_NAME}.ascii_files
}
saif_map -type ptpx -write_map ${OUTPUTS_DIR}/${COMPILE_BLOCK_NAME}.saif.ptpx.map

## write_scan_def must be excuted before save_block to update scandef
write_scan_def -output ${OUTPUTS_DIR}/${COMPILE_BLOCK_NAME}.scandef

## write test protocols and dft_drc for each DFT mode
#foreach mode $DFT_MODES_LIST {
#    redirect -tee $REPORTS_DIR/$COMPILE_BLOCK_NAME.$mode.dft_drc {dft_drc -test_mode $mode}
#    write_test_protocol -test_mode $mode -output $OUTPUTS_DIR/$COMPILE_BLOCK_NAME.$mode.spf
#}

save_block
if {$UNIFIED_FLOW} {
    save_block -as ${DESIGN_NAME}/${PLACE_OPT_BLOCK_NAME}
}

	####################################
	### Create abstract and frame
	#####################################
	### Enabled for hierarchical designs; for bottom and intermediate levels of physical hierarchy
	
	if {$DESIGN_STYLE == "hier"} {
	        if {$USE_ABSTRACTS_FOR_POWER_ANALYSIS == "true"} {
	                set_app_options -name abstract.annotate_power -value true
	        }
	}
	## Option to skip to avoid runtime overhead for unused abstracts
	if { !$SKIP_ABSTRACT_GENERATION } {
	  if { $PHYSICAL_HIERARCHY_LEVEL == "intermediate" && $ABSTRACT_TYPE_FOR_MPH_BLOCKS == "flattened" } {
	    create_abstract -read_only -preserve_block_instances false
	  } else {
	    create_abstract -read_only
	  }
	  create_frame -block_all true
	}
	
	write_test_model  -output ${OUTPUTS_DIR}/${DESIGN_NAME}.ctl

set_svf -off

###########################################################################################
## Report and output
###########################################################################################
#redirect -file ${REPORTS_DIR}/${REPORT_PREFIX}.check_legality {check_legality -verbose}

###########################################################################################
# Write Ideal timing rpts
###########################################################################################
write_sdc -version 2.1 -nosplit -output ${DESIGN_NAME}.sdc

#remove_clocks -all
#read_sdc ${DESIGN_NAME}.sdc

###########################################################################################
## Report and output
###########################################################################################
source -echo -verbose  ../../block_files/utils/fc_report_timing_level.tcl
sh mkdir -p $REPORTS_DIR/compile_timing_levels

proc_qor > ${REPORTS_DIR}/compile.qor.qor
report_transformed_registers -nosplit >  ${REPORTS_DIR}/compile.report_transformed_registers

if {$REPORT_QOR} {
		## Classic reporting
		source report_qor.tcl
                foreach_in_collection  group [get_path_groups] {
                        set group_name  [get_object_name $group]
			report_timing -groups $group_name -max 1000 -sort_by slack -slack_lesser_than 0.1 -nets -input_pins -nosplit -signifi 3 -transition_time -capacitance -physical >  $REPORTS_DIR/${REPORT_PREFIX}.${group_name}
#			report_timing -groups $group_name -start_end_pair -sort_by slack -slack_lesser_than 0.1 -nets -input_pins -nosplit -signifi 3 -transition_time -capacitance >  $REPORTS_DIR/${REPORT_PREFIX}.${group_name}
                }
                foreach_in_collection  group [get_path_groups] {
                        set group_name  [get_object_name $group]
			fc_report_timing_level -group $group_name -max 1000 -slack_lt 0.010 -extras > $REPORTS_DIR/compile_timing_levels/timing_level_$group_name.rpt
                }
}
###########################################################################################
# To get the block-wise flop count
source -echo -verbose  ../../block_files/utils/flop_count.tcl
report_threshold_voltage_groups > $REPORTS_DIR/compile.report_threshold_voltage_groups
# To report block-wise combinational area
#source -echo -verbose  ../../block_files/utils/block_area.tcl

# To report deep-dive timing report in .csv format
foreach_in_collection group [get_path_groups ] {       
report_timing -groups $group -max 100000 -sort_by slack -slack_lesser_than 0 -nets -input_pins -nosplit -signifi 3 -transition_time -capacitance -physical  
} >> all_timing_paths_compile
#source -echo -verbose  ../../block_files/utils/tp_attr.tcl
report_min_pulse_width > ${REPORTS_DIR}/compile.report_min_pulse_width_vio.rpt
# Check if any ports are unconstrained
check_timing > ../rpts/compile/compile.check_timing
#sh grep TCK-001 ../rpts/compile/compile.check_timing | grep -v SE | grep -v SI | grep -v D > ../rpts/compile/compile.check_timing_only_ports
report_area -hierarchy > ${REPORTS_DIR}/compile.report_area_hier
report_cross_probing -nosplit -original_hierarchy [get_flat_cells ] > ${REPORTS_DIR}/compile.report_cross_probing
report_msg -summary
print_message_info -ids * -summary
echo [date] > compile

exit
