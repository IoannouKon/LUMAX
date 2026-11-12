# Vivado batch entry point. Chipyard has already generated the RTL and IP Tcl.
if {[llength $argv] != 7} {
  error "Expected: top manifest common_tcl board_tcl checkpoint saif saif_scope"
}
lassign $argv top manifest scriptdir board_tcl checkpoint saif saif_scope
set rptdir [file normalize reports]
file mkdir $rptdir
source $board_tcl
set_param general.maxThreads $::env(LUMAX_VIVADO_JOBS)
set accelerator lumax
if {[info exists ::env(LUMAX_VIVADO_ACCELERATOR)]} {
  set accelerator $::env(LUMAX_VIVADO_ACCELERATOR)
}
set reference_name [expr {$accelerator eq "gemmini" ? "Gemmini" : "LUMAXExample"}]

proc accelerator_cell {reference_name} {
  set cells [get_cells -quiet -hierarchical -filter [format {REF_NAME =~ %s*} $reference_name]]
  # Exact module name is preferred over numbered variants of nested modules.
  set exact [get_cells -quiet -hierarchical -filter [format {REF_NAME == %s} $reference_name]]
  if {[llength $exact] == 1} { return $exact }
  if {[llength $cells] != 1} {
    error "Expected one $reference_name accelerator instance, found: $cells"
  }
  return $cells
}

if {[file isfile $checkpoint]} {
  open_checkpoint $checkpoint
} else {
  set wrkdir [file normalize obj]
  set ipdir [file join $wrkdir ip]
  set boarddir [file dirname $board_tcl]
  set srcdir [file dirname $manifest]
  set ip_vivado_tcls [lsort [glob -directory $srcdir *.vivado.tcl]]
  create_project -in_memory -part $part_fpga $top
  set_property TARGET_LANGUAGE Verilog [current_project]

  set input [open $manifest r]
  set sources [split [string trim [read $input]] "\n"]
  close $input
  set rtl_files {}
  foreach source_file $sources {
    set source_file [string trim $source_file]
    if {$source_file eq "" || [string index $source_file 0] eq "#"} {
      continue
    }
    if {[file pathtype $source_file] ne "absolute"} {
      set source_file [file join $srcdir $source_file]
    }
    lappend rtl_files $source_file
  }
  add_files -norecurse $rtl_files

  source [file join $scriptdir init.tcl]
  set_property include_dirs $ip_include_dirs [current_fileset]
  # Keep a named accelerator boundary for utilization, timing and power.
  synth_design -top $top -flatten_hierarchy none
  write_checkpoint -force [file join $wrkdir post_synth.dcp]
  # Retain synthesis resources if a larger design cannot be placed or routed.
  report_utilization -file [file join $rptdir synthesis_system_utilization.rpt]
  report_utilization -cells [accelerator_cell $reference_name] -file [file join $rptdir synthesis_${accelerator}_utilization.rpt]
  opt_design
  place_design
  phys_opt_design
  route_design
  if {![report_route_status -boolean_check ROUTED_FULLY] ||
      [report_route_status -boolean_check ERRORS_IN_ROUTES]} {
    report_route_status -file [file join $rptdir routing.rpt]
    error "ZCU106 routing did not complete successfully"
  }
  set temp_checkpoint [file join $wrkdir post_route.dcp]
  write_checkpoint -force $temp_checkpoint
  file rename -force $temp_checkpoint $checkpoint
}

report_route_status -file [file join $rptdir routing.rpt]
report_utilization -file [file join $rptdir system_utilization.rpt]
report_utilization -hierarchical -file [file join $rptdir hierarchical_utilization.rpt]
report_ram_utilization -file [file join $rptdir ram_utilization.rpt]
report_clocks -file [file join $rptdir clocks.rpt]
report_timing_summary -report_unconstrained -max_paths 5 -file [file join $rptdir system_timing.rpt]

set lumax [accelerator_cell $reference_name]
set lumax_name [get_property NAME $lumax]
report_utilization -cells $lumax -file [file join $rptdir ${accelerator}_utilization.rpt]
report_ram_utilization -cells $lumax -include_lutram -file [file join $rptdir ${accelerator}_ram_utilization.rpt]

set registers [get_cells -hierarchical -filter [format {IS_SEQUENTIAL && NAME =~ "%s/*"} $lumax_name]]
if {[llength $registers] == 0} {
  error "No sequential cells found under $lumax_name"
}
set internal_report [file join $rptdir ${accelerator}_internal_timing.rpt]
set boundary_report [file join $rptdir ${accelerator}_boundary_timing.rpt]
set pins [get_pins -of_objects $lumax -filter {REF_PIN_NAME != clock && REF_PIN_NAME != reset}]
foreach delay_type {max min} {
  set append_flag [expr {$delay_type eq "min" ? "-append" : ""}]
  report_timing -from $registers -to $registers -delay_type $delay_type -max_paths 5 \
    -file $internal_report {*}$append_flag
  # A path through an interface pin crosses the accelerator boundary.
  report_timing -through $pins -delay_type $delay_type -max_paths 5 \
    -file $boundary_report {*}$append_flag
}

if {$saif ne ""} {
  if {$saif_scope eq ""} {
    read_saif -out_file [file join $rptdir saif_unmatched.txt] $saif
  } else {
    read_saif -strip_path $saif_scope -out_file [file join $rptdir saif_unmatched.txt] $saif
  }
}
set depth [expr {[llength [split $lumax_name /]] + 1}]
report_power -hierarchical_depth $depth -file [file join $rptdir power.rpt]

set summary [open [file join $rptdir summary.tsv] w]
puts $summary "metric\tvalue"
puts $summary "fpga_part\t$part_fpga"
puts $summary "${accelerator}_instance\t$lumax_name"
puts $summary "clock_target_mhz\t$::env(LUMAX_ZCU106_CLOCK_MHZ)"
puts $summary "power_activity\t[expr {$saif eq "" ? "vectorless" : "SAIF with vectorless fallback"}]"
foreach {scope args} [list system {} ${accelerator}_internal [list -from $registers -to $registers] ${accelerator}_boundary [list -through $pins]] {
  foreach {delay_type check} {max setup min hold} {
    set paths [get_timing_paths -quiet -delay_type $delay_type -max_paths 1 {*}$args]
    if {[llength $paths]} {
      puts $summary "${scope}_${check}_slack_ns\t[get_property SLACK $paths]"
    }
  }
}
set path [get_timing_paths -quiet -from $registers -to $registers -delay_type max -max_paths 1]
if {[llength $path] == 1} {
  set clock [get_property STARTPOINT_CLOCK $path]
  set endpoint_clock [get_property ENDPOINT_CLOCK $path]
  set requirement [get_property REQUIREMENT $path]
  set slack [get_property SLACK $path]
  if {$clock ne "" && $clock eq $endpoint_clock} {
    set period [get_property PERIOD [get_clocks $clock]]
    puts $summary "${accelerator}_clock\t$clock"
    puts $summary "${accelerator}_clock_period_ns\t$period"
    # Same-clock single-cycle path estimate for this routed design.
    if {abs($requirement - $period) < 0.001 && $period > $slack} {
      puts $summary [format "${accelerator}_internal_estimated_fmax_mhz\t%.3f" [expr {1000.0 / ($period - $slack)}]]
    }
  }
}
close $summary
