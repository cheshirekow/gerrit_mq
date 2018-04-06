"use strict";

// If the stream is stalled waiting for the log to flush, then wait this
// long before trying again.
var kPollPeriod = 5000;

// NOTE(josh): set to something like 100 to test streaming of large
// uncompressed logs.
var kMaxIncrementSize = 512 * 1024;

// if a <pre> contains more than this many lines, then split it out and create
// a new <pre>
var kLinesPerElement = 100;

// Base URL for gerrit links
var kGerritURL = "https://localhost:8443";

// Number of (history or queue) items to show per page by default
var kDefaultItemsPerPage = 50;

// Parse the URI query string and return a json object mapping query keys
// to their values. Also sets certain keys to their default values if they
// are not in the URI query.
function get_query_as_object() {
  var query_str = location.search.substring(1);
  var query_obj = {};
  query_str.replace(/([^=&]+)=([^&]*)/g, function(m, key, value) {
    query_obj[decodeURIComponent(key)] = decodeURIComponent(value);
  });

  if("page" in query_obj) {
    query_obj.page = parseInt(query_obj.page);
  } else {
    query_obj.page = 0;
  }

  if("page_size" in query_obj) {
    query_obj.page_size = parseInt(query_obj.page_size);
  } else {
    query_obj.page_size = kDefaultItemsPerPage;
  }

  if("merge_id" in query_obj) {
    query_obj.merge_id = parseInt(query_obj.merge_id);
  } else {
    query_obj.merge_id = -1;
  }

  if("follow_stream" in query_obj) {
    query_obj.follow_stream = (query_obj.follow_stream == "true");
  } else {
    query_obj.follow_stream = true;
  }

  return query_obj;
}

// Set pager link URLs based on pager setup.
function set_pagination(current_page, total_pages, page_size) {
  var prev_page = 0;
  if(current_page > 0){
    prev_page = current_page - 1;
  }

  var next_page = current_page + 1;
  var last_page = next_page;

  if(total_pages !== null) {
    last_page = (total_pages -1);
    if(next_page > last_page) {
      next_page = last_page;
    }
  }

  document.getElementById("first_page_anchor").href =
    "?page=0&page_size=" + page_size;
  document.getElementById("prev_page_anchor").href =
    "?page=" + prev_page + "&page_size=" + page_size;
  document.getElementById("next_page_anchor").href =
    "?page=" + next_page + "&page_size=" + page_size;
  document.getElementById("last_page_anchor").href =
    "?page=" + last_page + "&page_size=" + page_size;
  document.getElementById("page_input").value =
    "" + current_page;
  document.getElementById("page_size_input").value =
    "" + page_size;
}


// find the kLinesPerElement'th occurance of a newline character and return
// it's index, if found, or -1
function get_split_index(text) {
  var split_index = text.indexOf("\n", 0);
  for(var delim_count = 0; delim_count < kLinesPerElement; delim_count++) {
    split_index = text.indexOf("\n", split_index + 1);
    if(split_index == -1) {
      return -1;
    }
  }

  return split_index;
}

// Add a block of text to the current stream, one "page" at a time. This
// greatly improves page responsiveness over appending directly to a single
// div, in which case the rendering engine must reflow a giant continuous
// block of text every time.
function add_text_to_stream(ctx, text, on_complete, timeout) {
  var total_text = ctx.element.innerHTML + text;

  // NOTE(josh): javascript split() doesn't work likepython split() and
  // if we specify a limit then we discard the entire string after that
  // many delimiters.
  var split_index = get_split_index(text);
  if(split_index == -1) {
    ctx.element.innerHTML += text;
    if(page_context.follow_stream
       && (ctx.extension == page_context.active_div)) {
      window.scrollTo(0, document.body.scrollHeight);
    }
    setTimeout(on_complete, timeout);
  } else {
    ctx.element.innerHTML = total_text.slice(0, split_index);
    var remainder = total_text.slice(split_index + 1);
    var old_pre = ctx.element;
    var new_pre = document.createElement("pre");

    old_pre.parentNode.insertBefore(new_pre, old_pre.nextSibling);
    ctx.element = new_pre;

    setTimeout(function(){
      add_text_to_stream(ctx, remainder, on_complete, timeout);
    }, 0);
  }
}



// The log we want has finished streaming and has been gzipped. We must
// fetch the whole thing at once.
function fetch_log_content(ctx) {
  var xhttp = new XMLHttpRequest();
  xhttp.onreadystatechange = function() {
    // TODO(josh): implement error handler!!
    if (this.readyState == 4 && this.status == 200) {
      add_text_to_stream(ctx, this.response);
    }
  };
  xhttp.open("GET", "/logs/" + ctx.numstr + "." + ctx.extension);
  xhttp.setRequestHeader("Accept", "application/octet-stream");
  xhttp.send();
}

// Fetch the next incremental bit of the log
function fetch_log_increment(ctx) {
  var bytes_requested = ctx.bytes_total - ctx.bytes_received;
  if(bytes_requested > kMaxIncrementSize) {
    bytes_requested = kMaxIncrementSize;
  }

  var xhttp = new XMLHttpRequest();
  xhttp.onreadystatechange = function() {
    // TODO(josh): implement error handler!!
    if (this.readyState == 4 && this.status == 200) {
      ctx.bytes_received +=
          parseInt(this.getResponseHeader("Content-Length"));

      var response_range = this.getResponseHeader("Content-Range");
      if(response_range === null){
        console.log("Unexpected Content-Range: " + response_range);
        add_text_to_stream(ctx, self.response,
          function(){fetch_log_header(ctx);},
          kPollPeriod);
        return;
      }

      var response_parts = response_range.split("/");
      if(response_parts.length != 2) {
        console.log("Unexpected Content-Range: " + response_range);
        add_text_to_stream(ctx, self.response,
          function(){fetch_log_header(ctx);},
          kPollPeriod);
        return;
      } else {
        ctx.bytes_total = parseInt(response_parts[1]);
        if(ctx.bytes_received == ctx.bytes_total) {
          // There is no more data available to be read, go back to
          // fetching just the header until there is more data to read
          add_text_to_stream(ctx, self.response,
            function(){fetch_log_header(ctx);}, 0);
        } else {
          add_text_to_stream(ctx, self.response,
            function(){fetch_log_increment(ctx);}, 0);
        }
      }
    }
  };
  xhttp.open("GET", "/logs/" + ctx.numstr + "." + ctx.extension);
  xhttp.setRequestHeader("Accept", "application/octet-stream");
  xhttp.setRequestHeader("Range",  "bytes=" + ctx.bytes_received + "-"
                                   + (ctx.bytes_received + bytes_requested));
  xhttp.send();
}

// Initial request just fetches headers which tells us wether or not the
// file is currently streaming or if it"s already completed and
// compressed (in which case we have to fetch the whole thing at once).
function fetch_log_header(ctx) {
  var xhttp = new XMLHttpRequest();
  xhttp.onreadystatechange = function() {
    // TODO(josh): implement error handler!!
    if (this.readyState == 4 && this.status == 200) {
      var accept_ranges = this.getResponseHeader("Accept-Ranges");
      var content_length = this.getResponseHeader("Content-Length");

      if(accept_ranges == "bytes"){
        // console.log("Server accepts byte ranges");
      } else {
        // TODO(josh): server will not accept byte ranges if file is too small,
        // so look at Content-Encoding to tell whether or not the transfer was
        // gzipped. If it was gzipped, then we are done. If not, then keep
        // polling.
        console.log("Server does not accept byte ranges: "
                    + accept_ranges + ", assuming complete gzipped log");
        console.log(this.getAllResponseHeaders());
        fetch_log_content(ctx);
        return;
      }

      if(content_length === null) {
        console.log("No content-length header!!");
        console.log(this.getAllResponseHeaders());
        console.log(this.url);
        return;
      } else {
        ctx.bytes_total = content_length;

        if(ctx.bytes_total > ctx.bytes_received) {
          // There are new bytes available to read, so start fetching
          // them.
          fetch_log_increment(ctx);
        } else {
          // We have read everything there is to read, so fetch the
          // headers again after one second to see if new data is
          // available.
          setTimeout(function(){
            fetch_log_header(ctx);
          }, kPollPeriod);
        }
      }
    }
  };
  xhttp.open("HEAD", "/logs/" + ctx.numstr + "." + ctx.extension);
  xhttp.setRequestHeader("Accept", "application/octet-stream");
  xhttp.send();
}

// Initialize a log stream context and start the fetch state machine.
function init_log_stream(numstr, extension) {
  var stream_dl = document.getElementById(extension + "_dl");
  stream_dl.href = "logs/" + numstr + "." + extension;
  stream_dl.style.display = "inline";

  var stream_pre = document.getElementById(extension + "_pre");
  var context = {
    numstr : numstr,
    extension : extension,
    element : stream_pre,
    bytes_received : 0,
    bytes_total : 0,
  };

  fetch_log_header(context);
  return context;
}


// Show a div and hide the rest
function show_div(event, div_name) {
  page_context.active_div = div_name;
  if(event !== null){
    event.preventDefault();
  }
  var div_names = ["log", "stdout", "stderr"];
  for(var i=0; i < 3; i++) {
    if(div_name == div_names[i]) {
      document.getElementById(div_names[i] + "_div")
        .style.display = "block";
    } else {
      document.getElementById(div_names[i] + "_div")
        .style.display = "none";
    }
  }
  return false;
}


// Enable/Disable stream following
function set_follow(event, value) {
  if(event != null) {
    event.preventDefault();
  }

  page_context.follow_stream = value;
  if(value) {
    window.scrollTo(0, document.body.scrollHeight);
  }
}

function cancel_merge(event, merge_id) {
  if(event != null) {
    event.preventDefault();
  }

  var xhttp = new XMLHttpRequest();
  xhttp.responseType = "json";
  xhttp.onreadystatechange = function() {
    // TODO(josh): implement error handler!!
    if (this.readyState == 4 && this.status == 200) {
      event.target.parentNode.removeChild(event.target);
    }
  };
  xhttp.open("GET", "/gmq/cancel_merge?rid=" + merge_id);
  xhttp.setRequestHeader("Accept", "application/json");
  xhttp.send();
}

function parse_datetime(date_str) {
  var datetime_parts = date_str.split(" ");
  var date = datetime_parts[0];
  var time = datetime_parts[1];

  var date_parts = date.split("-");
  var time_parts = time.split(":");

  return Date.UTC(parseInt(date_parts[0]),
    parseInt(date_parts[1]) - 1,
    parseInt(date_parts[2]),
    parseInt(time_parts[0]),
    parseInt(time_parts[1]),
    parseInt(time_parts[2]));
}

var kStatusMap = {
  "-3" : "TIMEOUT",
  "-2" : "CANCELED",
  "-1" : "STEP FAILED",
  "0" : "SUCCESS",
  "1" : "IN PROGRESS"
};

function get_status_string(status_code) {
  if(status_code in kStatusMap) {
    return kStatusMap[status_code];
  } else {
    return "??";
  }
}

function get(obj, attr, def) {
  if(obj === undefined) {
    return def;
  }

  if(attr in obj) {
    return obj[attr];
  } else {
    return def;
  }
}

function render_details(ctx, merge) {
  var start_time = Date.parse(merge.start_time) / 1000;
  var end_time = Date.parse(merge.end_time) / 1000;
  var merge_duration = format_duration(end_time - start_time);

  var args = {
    status_text : get_status_string(merge.status),
    cancel_style : "display: none",
    result_class : "",
    merge : merge,
    merge_duration : merge_duration,
  };

  if(merge.status == 0) {
    args.result_class = "success";
  } else if (merge.status == 1) {
    args.result_class = "inprogress";
    args.cancel_style = "";
  } else {
    args.result_class = "failure";
  }

  ctx.details.elem.innerHTML = Mustache.to_html(ctx.details.tpl, args);
  ctx.changes.elem.innerHTML = "";

  for(var idx in merge.changes) {
    var change = merge.changes[idx];
    var request_time = Date.parse(change.request_time) / 1000;
    var queue_duration = format_duration(start_time - request_time);
    var feature_branch = get(change.metadata, "Feature-Branch", "<null>");

    args = {gerrit_url : kGerritURL,
      change : change,
      feature_branch : feature_branch,
      queue_duration : queue_duration};

    ctx.changes.elem.insertRow(-1).innerHTML =
        Mustache.to_html(ctx.changes.tpl, args);
  }
}

// Start an async request to fetch details and then render them with Mustache.
function fetch_details(merge_id) {
  var query_url = "";
  if(merge_id == -1) {
    query_url = "/gmq/get_merge_status";
  } else {
    query_url = "/gmq/get_merge_status?rid=" + merge_id;
  }

  var context = {
    details : {
      tpl : document.getElementById("details_tpl").innerHTML,
      elem : document.getElementById("details_tbl"),
    },
    changes : {
      tpl : document.getElementById("change_tpl").innerHTML,
      elem : document.getElementById("changes_tbl"),
    }
  };

  var xhttp = new XMLHttpRequest();
  xhttp.responseType = "json";
  xhttp.onreadystatechange = function() {
    // TODO(josh): implement error handler!!
    if (this.readyState == 4 && this.status == 200) {
      var merge = this.response;
      render_details(context, merge);

      var numstr = "" + merge.rid;
      while(numstr.length < 6) {
        numstr = "0" + numstr;
      }

      init_log_stream(numstr, "log");
      init_log_stream(numstr, "stdout");
      init_log_stream(numstr, "stderr");

      var merge_duration_cell = document.getElementById("merge_duration");
      var start_time = parse_datetime(merge.start_time) / 1000;
      if(merge.status == 1) {
        var update_duration = function(){
          var end_time = Date.now() / 1000;
          merge_duration_cell.innerHTML =
             format_duration(end_time - start_time);
        };
        update_duration();
        setInterval(update_duration, 1000);
      } else {
        var end_time = parse_datetime(merge.end_time) / 1000;
        merge_duration_cell.innerHTML =
           format_duration(end_time - start_time);
      }
    }
  };
  xhttp.open("GET", query_url);
  xhttp.setRequestHeader("Accept", "application/json");
  xhttp.send();
}


function format_duration(duration_seconds) {
  var hours   = Math.floor(duration_seconds / 3600);
  var minutes = Math.floor((duration_seconds - (hours * 3600)) / 60);
  var seconds = duration_seconds - (hours * 3600) - (minutes * 60);
  seconds = Math.floor(seconds * 100) / 100;

  if (hours   < 10) {hours   = "0"+hours;}
  if (minutes < 10) {minutes = "0"+minutes;}
  if (seconds < 10) {seconds = "0"+seconds;}

  if(hours != "00") {
    return hours+"h"+minutes+"m"+seconds+"s";
  } else if(minutes != "00") {
    return minutes+"m"+seconds+"s";
  } else {
    return seconds+"s";
  }
}


function compute_durations(merge, change) {
  if(merge === undefined || change === undefined) {
    return {
      queue: "",
      merge: ""
    };
  }

  var request_time = Date.parse(change.request_time) / 1000;
  var start_time = Date.parse(merge.start_time) / 1000;
  var end_time = Date.parse(merge.end_time) / 1000;

  return {
    queue : format_duration(start_time - request_time),
    merge : format_duration(end_time - start_time),
  };
}


function update_build_duration(start_time, cell) {
  cell.innerHTML = format_duration(Date().getTime() - start_time);
}


// Callback for when history page data is received from webfront
function render_history(query_obj, data, page_context) {
  var num_pages = Math.ceil(data.count / query_obj.page_size);
  set_pagination(query_obj.page, num_pages, query_obj.page_size);

  var head_template = document.getElementById("history_head_tpl").innerHTML;
  var tail_template = document.getElementById("history_tail_tpl").innerHTML;

  var history_table = document.getElementById("history_table");
  history_table.innerHTML = "";

  for(var idx in data.result) {
    var merge = data.result[idx];
    var row_class = "failure";

    if(merge.status == 0) {
      row_class = "success";
    } else if(merge.status == 1) {
      row_class = "inprogress";
    }

    var change0 = merge.changes[0];
    var args = {
      merge : merge,
      status_text : get_status_string(merge.status),
      change : change0,
      feature_branch : get(
        get(change0, "metadata", {}),
        "Feature-Branch", "<null>"),
      rowspan : merge.changes.length,
      gerrit_url : kGerritURL,
      durations : compute_durations(merge, change0)
    };

    var new_row = history_table.insertRow(-1);
    new_row.innerHTML = Mustache.to_html(head_template, args);
    new_row.className = row_class;

    if(query_obj.page == 0 && idx == 0 && merge.status == 1) {
      var start_time = parse_datetime(merge.start_time) / 1000;
      var duration_cell = new_row.cells[new_row.cells.length-1];
      duration_cell.innerHTML = "";
      duration_cell.insertBefore(page_context.duration_elems[0], null);
    }

    for(var jdx=1; jdx < merge.changes.length; jdx++) {
      var change = merge.changes[jdx];
      args = {
        change : change,
        feature_branch : get(change.metadata, "Feature-Branch", "<null>"),
        gerrit_url : kGerritURL,
        durations : compute_durations(merge, change)
      };

      new_row = history_table.insertRow(-1);
      new_row.innerHTML = Mustache.to_html(tail_template, args);
      new_row.className = row_class;
    }
  }
}

function fetch_and_render_history(query_obj, page_context) {
  var xhttp = new XMLHttpRequest();
  xhttp.responseType = "json";
  xhttp.onreadystatechange = function() {
    // TODO(josh): implement error handler!!
    if (this.readyState == 4 && this.status == 200) {
      render_history(query_obj, this.response, page_context);
    }
  };

  var row_offset = query_obj.page * query_obj.page_size;
  xhttp.open("GET", "/gmq/get_history?offset=" + row_offset
                    + "&limit=" + query_obj.page_size);
  xhttp.setRequestHeader("Accept", "application/json");
  xhttp.send();
}

function poll_current_merge(query_obj, page_context) {
  // most recent merge has finished, poll the history to see if a new
  // merge has started
  var xhttp = new XMLHttpRequest();
  xhttp.responseType = "json";
  xhttp.onreadystatechange = function() {
    // TODO(josh): implement error handler!!
    if (this.readyState == 4 && this.status == 200) {
      var current_merge = this.response;

      if(page_context.current_merge === null
        || (page_context.current_merge.rid != current_merge.rid)
        || (page_context.current_merge.status != current_merge.status)) {
        page_context.current_merge = current_merge;

        if(current_merge.status == 1) {
          render_details(page_context, current_merge);
          page_context.current_merge_div.style.display = "inline";

          // new_table = page_context.current_merge_div.childNodes[3];
          // new_table.rows[4].cells[4].insertBefore(
          //   page_context.duration_elems[1], null);
        } else {
          page_context.current_merge_div.style.display = "none";
        }

        page_context.refresh();
      }
    }
  };

  xhttp.open("GET", "/gmq/get_active_merge_status");
  xhttp.setRequestHeader("Accept", "application/json");
  xhttp.send();
}

function get_merge_duration(record) {
  var start_time = parse_datetime(record.start_time) / 1000;
  var end_time = Date.now() / 1000;
  return end_time - start_time;
}

function update_durations(page_context) {
  if(page_context.current_merge !== null) {
    var duration = get_merge_duration(page_context.current_merge);
    var duration_str = format_duration(duration);
    for(var idx in page_context.duration_elems) {
      page_context.duration_elems[idx].innerHTML = duration_str;
    }
  }
}

// Called when the history page is ready
function history_page_ready() {
  var query_obj = get_query_as_object();
  set_pagination(query_obj.page, null, query_obj.page_size);

  var page_context = {
    current_merge : null,
    current_merge_div :
      document.getElementById("current_merge_div"),
    duration_elems : [
      document.createElement("span"),
      document.createElement("span"),
    ],
    query_obj : query_obj,

    details : {
      tpl : document.getElementById("details_tpl").innerHTML,
      elem : document.getElementById("details_tbl"),
    },
    changes : {
      tpl : document.getElementById("change_tpl").innerHTML,
      elem : document.getElementById("changes_tbl"),
    },
    refresh : function() {
      fetch_and_render_history(this.query_obj, this);
    }
  };

  document.getElementById("merge_duration")
    .insertBefore(page_context.duration_elems[1], null);

  poll_current_merge(query_obj, page_context);
  setInterval(function() {
    poll_current_merge(query_obj, page_context);
  }, 5000);

  setInterval(function() {
    update_durations(page_context);
  }, 900);
}


// Callback for when current queue data is received from webfront
function render_queue(query_obj, data) {
  var num_pages = Math.ceil(data.count / query_obj.page_size);
  set_pagination(query_obj.page, num_pages, query_obj.page_size);

  var template = document.getElementById("row_tpl").innerHTML;
  var history_table = document.getElementById("history_table");
  history_table.innerHTML = "";

  for(var idx in data.result) {
    var change_info = data.result[idx];
    change_info.queue_index = idx;
    if ("Feature-Branch" in change_info.message_meta){
      change_info.feature_branch =
        change_info.message_meta["Feature-Branch"];
    } else {
      change_info.feature_branch = "null";
    }
    var new_row = history_table.insertRow(-1);
    change_info.gerrit_url = kGerritURL;
    new_row.innerHTML = Mustache.to_html(template, change_info);
  }
}

function fetch_and_render_queue(query_obj) {
  var xhttp = new XMLHttpRequest();
  xhttp.responseType = "json";
  xhttp.onreadystatechange = function() {
    // TODO(josh): implement error handler!!
    if (this.readyState == 4 && this.status == 200) {
      render_queue(query_obj, this.response);
    }
  };

  var query_url = "/gmq/get_queue?offset="
                  + (query_obj.page * query_obj.page_size)
                  + "&limit=" + query_obj.page_size;
  xhttp.open("GET", query_url);
  xhttp.setRequestHeader("Accept", "application/json");
  xhttp.send();
}

// Called when the queue page is ready
function queue_page_ready() {
  var query_obj = get_query_as_object();

  set_pagination(query_obj.page, null, query_obj.page_size);

  var page_context = {
    current_merge : null,
    current_merge_div :
      document.getElementById("current_merge_div"),
    duration_elems : [
      document.createElement("span"),
      document.createElement("span"),
    ],
    query_obj : query_obj,

    details : {
      tpl : document.getElementById("details_tpl").innerHTML,
      elem : document.getElementById("details_tbl"),
    },
    changes : {
      tpl : document.getElementById("change_tpl").innerHTML,
      elem : document.getElementById("changes_tbl"),
    },
    refresh : function() {
      fetch_and_render_queue(this.query_obj);
    }
  };

  document.getElementById("merge_duration")
    .insertBefore(page_context.duration_elems[1], null);

  poll_current_merge(query_obj, page_context);
  setInterval(function() {
    poll_current_merge(query_obj, page_context);
  }, 5000);

  setInterval(function() {
    update_durations(page_context);
  }, 900);
}



function set_pause_view(elems, value) {
  if(value) {
    elems.paused.className = "inprogress";
    elems.paused.innerHTML = value;
    elems.pause_button.innerHTML = "[resume]";
    elems.pause_button.onclick = function(event){
      pause_daemon(event, false);
      return false;
    };
  } else {
    elems.paused.className = "success";
    elems.paused.innerHTML = value;
    elems.pause_button.innerHTML = "[pause]";
    elems.pause_button.onclick = function(event){
      pause_daemon(event, true);
      return false;
    };
  }
}

function handle_daemon_status(elems, data) {
  elems.alive.innerHTML = data.alive;
  if(data.alive) {
    elems.alive.className = "success";
  } else {
    elems.alive.className = "failure";
  }

  set_pause_view(elems, data.paused);
  elems.pid.innerHTML = data.pid;
}

function pause_daemon(event, value) {
  var elems = {
    alive : document.getElementById("alive_cell"),
    paused : document.getElementById("paused_cell"),
    pid : document.getElementById("pid_cell"),
    pause_button : document.getElementById("pause_button"),
  };

  if(event !== null) {
    event.preventDefault();
  }

  var xhttp = new XMLHttpRequest();
  xhttp.responseType = "json";
  xhttp.onreadystatechange = function() {
    if (this.readyState == 4 && this.status == 200) {
      handle_daemon_status(elems, this.response);
    }
  };

  xhttp.open("GET", "/gmq/set_daemon_pause?value=" + value);
  xhttp.setRequestHeader("Accept", "application/json");
  xhttp.send();
}

function query_daemon_status() {
  var elems = {
    alive : document.getElementById("alive_cell"),
    paused : document.getElementById("paused_cell"),
    pid : document.getElementById("pid_cell"),
    pause_button : document.getElementById("pause_button"),
  };

  var xhttp = new XMLHttpRequest();
  xhttp.responseType = "json";
  xhttp.onreadystatechange = function() {
    if (this.readyState == 4 && this.status == 200) {
      handle_daemon_status(elems, this.response);
    }
  };

  xhttp.open("GET", "/gmq/get_daemon_status");
  xhttp.setRequestHeader("Accept", "application/json");
  xhttp.send();
}

// Called when the daemon page is ready
function daemon_page_ready() {
  query_daemon_status();
  setInterval(query_daemon_status, 10000);
}

function on_ready(callback) {
  // in case the document is already rendered / event has already fired
  if (document.readyState != "loading") {
    setTimeout(callback, 10);
  // modern browsers
  } else if (document.addEventListener) {
    document.addEventListener("DOMContentLoaded", callback);
  // IE <= 8
  } else {
    document.attachEvent("onreadystatechange", function(){
      if(document.readyState == "complete") {
        callback();
      }
    });
  }
}
