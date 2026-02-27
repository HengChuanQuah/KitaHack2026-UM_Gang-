import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:http/http.dart' as http;
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'dart:convert';

import 'package:url_launcher/url_launcher.dart';

// ✅ NEW: video player for Visualise button
import 'package:video_player/video_player.dart';

void main() =>
    runApp(const MaterialApp(home: HomeScreen(), debugShowCheckedModeBanner: false));

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  String? selectedSubject;
  List<String> subjects = [];
  List<String> savedFiles = [];
  List<String> scopeTopics = [];
  Map<String, bool> topicProgress = {};
  List<dynamic> mockQuestions = [];

  List<String> noteFiles = [];

  int activeView = 0;

  // Old markdown fallback
  String currentReadingContent = "";

  // ✅ NEW: parsed questions for Option 1
  List<dynamic> analysisQuestions = [];
  String currentPyqFilename = "";

  List<PlatformFile> notesFiles = [];
  List<PlatformFile> pyqFiles = [];

  bool isLoading = false;

  final String serverUrl = "http://127.0.0.1:8000";
  // final String serverUrl = "https://felicia-hallowed-panoramically.ngrok-free.dev";

  @override
  void initState() {
    super.initState();
    fetchSubjects();
  }

  Future<void> fetchSubjects() async {
    try {
      final res = await http.get(Uri.parse('$serverUrl/subjects'));
      if (!mounted) return;
      if (res.statusCode == 200) {
        setState(() => subjects = List<String>.from(jsonDecode(res.body)['subjects']));
      }
    } catch (e) {
      debugPrint("Error fetching subjects: $e");
    }
  }

  Future<void> fetchSavedFiles() async {
    if (selectedSubject == null) return;
    try {
      final res = await http.get(Uri.parse('$serverUrl/subjects/$selectedSubject/files'));
      if (!mounted) return;
      if (res.statusCode == 200) {
        setState(() {
          savedFiles = List<String>.from(jsonDecode(res.body)['files']);
          activeView = 0;
        });
      }
    } catch (e) {
      debugPrint("Error fetching files: $e");
    }
  }

  Future<void> fetchNotes() async {
    if (selectedSubject == null) return;
    setState(() => isLoading = true);
    try {
      final res = await http.get(Uri.parse('$serverUrl/subjects/$selectedSubject/notes'));
      if (!mounted) return;
      if (res.statusCode == 200) {
        setState(() {
          noteFiles = List<String>.from(jsonDecode(res.body)['notes']);
          activeView = 5;
          isLoading = false;
        });
      } else {
        setState(() => isLoading = false);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Error loading notes: ${res.body}")),
        );
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => isLoading = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Error loading notes: $e")),
      );
    }
  }

  // ✅ Option 1: read JSON file instead of only markdown
  Future<void> readFile(String filename) async {
    if (selectedSubject == null) return;

    setState(() {
      isLoading = true;
      analysisQuestions = [];
      currentReadingContent = "";
      currentPyqFilename = filename;
    });

    // Convert txt filename -> json filename
    final jsonName = filename.replaceAll("_analysis.txt", "_analysis.json");

    try {
      final res = await http.get(
        Uri.parse('$serverUrl/subjects/$selectedSubject/analysis_json/$jsonName'),
      );
      if (!mounted) return;

      if (res.statusCode == 200) {
        final data = jsonDecode(res.body);

        // ✅ Only treat as valid if "questions" exists and is a List
        if (data is Map && data["questions"] is List) {
          final qs = data["questions"] as List;
          if (qs.isNotEmpty) {
            setState(() {
              analysisQuestions = qs;
              activeView = 2;
              isLoading = false;
            });
            return;
          }
        }
        // If JSON exists but empty/invalid, fall through to TXT fallback
      }
    } catch (_) {
      // fall back to text below
    }

    // Fallback: old text file rendering
    try {
      final res2 =
          await http.get(Uri.parse('$serverUrl/subjects/$selectedSubject/files/$filename'));
      if (!mounted) return;
      if (res2.statusCode == 200) {
        setState(() {
          currentReadingContent = jsonDecode(res2.body)['content'];
          activeView = 2;
          isLoading = false;
        });
      } else {
        setState(() => isLoading = false);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => isLoading = false);
    }
  }

  Future<void> openNoteExternally(String filename) async {
    if (selectedSubject == null) return;
    final url = Uri.parse('$serverUrl/subjects/$selectedSubject/notes/$filename');

    final ok = await launchUrl(url, mode: LaunchMode.externalApplication);
    if (!mounted) return;
    if (!ok) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text("Could not open PDF externally.")),
      );
    }
  }

  Future<void> uploadNotes() async {
    if (notesFiles.isEmpty || selectedSubject == null) return;

    for (final f in notesFiles) {
      if (f.bytes == null) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text("Lecture note file error. Please re-pick files.")),
        );
        return;
      }
    }

    setState(() => isLoading = true);

    try {
      var req = http.MultipartRequest(
        'POST',
        Uri.parse('$serverUrl/subjects/upload_notes'),
      );

      req.fields['subject'] = selectedSubject!;

      for (final f in notesFiles) {
        req.files.add(http.MultipartFile.fromBytes(
          'notes_files',
          f.bytes!,
          filename: f.name,
        ));
      }

      var streamedRes = await req.send();
      if (!mounted) return;

      var res = await http.Response.fromStream(streamedRes);
      if (!mounted) return;

      if (res.statusCode == 200) {
        String msg = "Notes uploaded!";
        try {
          msg = jsonDecode(res.body)['result'] ?? msg;
        } catch (_) {}

        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
        setState(() => notesFiles = []);
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Error: ${res.body}")),
        );
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Connection Error: $e")),
      );
    } finally {
      if (mounted) setState(() => isLoading = false);
    }
  }

  Future<void> uploadPyqAndAnalyze() async {
    if (pyqFiles.isEmpty || selectedSubject == null) return;

    for (final f in pyqFiles) {
      if (f.bytes == null) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text("PYQ file error. Please re-pick files.")),
        );
        return;
      }
    }

    setState(() => isLoading = true);

    try {
      var req = http.MultipartRequest(
        'POST',
        Uri.parse('$serverUrl/upload_and_analyze'),
      );

      req.fields['subject'] = selectedSubject!;

      for (final f in pyqFiles) {
        req.files.add(http.MultipartFile.fromBytes(
          'pyq_files',
          f.bytes!,
          filename: f.name,
        ));
      }

      var streamedRes = await req.send();
      if (!mounted) return;

      var res = await http.Response.fromStream(streamedRes);
      if (!mounted) return;

      if (res.statusCode == 200) {
        await fetchSavedFiles();
        if (!mounted) return;

        String msg = "Analysis complete!";
        try {
          msg = jsonDecode(res.body)['result'] ?? msg;
        } catch (_) {}

        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
        setState(() => pyqFiles = []);
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Error: ${res.body}")),
        );
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Connection Error: $e")),
      );
    } finally {
      if (mounted) setState(() => isLoading = false);
    }
  }

  // ✅ NEW: generate scope checklist once (backend), then load it
  Future<void> generateScopeChecklist() async {
    if (selectedSubject == null) return;
    setState(() => isLoading = true);

    try {
      await http.post(
        Uri.parse('$serverUrl/subjects/generate_scope'),
        body: {'subject_name': selectedSubject!},
      );
      if (!mounted) return;
      setState(() => isLoading = false);

      await fetchScope();
    } catch (e) {
      if (!mounted) return;
      setState(() => isLoading = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Error: $e")),
      );
    }
  }

  // ✅ UPDATED: fetch scope from backend scope file (with completed status)
  Future<void> fetchScope() async {
    if (selectedSubject == null) return;
    setState(() => isLoading = true);

    try {
      var res = await http.post(
        Uri.parse('$serverUrl/subjects/get_scope'),
        body: {'subject_name': selectedSubject!},
      );

      if (!mounted) return;

      if (res.statusCode == 200) {
        var data = jsonDecode(res.body);
        final scope = data['scope'];

        if (scope is List) {
          setState(() {
            scopeTopics = scope.map<String>((e) => e['topic'].toString()).toList();
            topicProgress = {
              for (final e in scope)
                e['topic'].toString(): (e['completed'] == true),
            };
            activeView = 3;
            isLoading = false;
          });
        } else {
          setState(() => isLoading = false);
        }
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Scope error: ${res.body}")),
        );
        setState(() => isLoading = false);
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Error: $e")),
      );
      setState(() => isLoading = false);
    }
  }

  Future<void> generateMockExam() async {
    if (selectedSubject == null) return;
    setState(() => isLoading = true);

    try {
      var res = await http.post(
        Uri.parse('$serverUrl/subjects/generate_mock'),
        body: {'subject': selectedSubject!},
      );

      if (!mounted) return;

      if (res.statusCode == 200) {
        var data = jsonDecode(res.body);
        setState(() {
          mockQuestions = data['questions'];
          activeView = 4;
          isLoading = false;
        });
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Error: ${res.body}")),
        );
        setState(() => isLoading = false);
      }
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Error: $e")),
      );
      setState(() => isLoading = false);
    }
  }

  // ✅ NEW: Clear all ticks (UI + backend)
  Future<void> clearAllScopeTicks() async {
    if (selectedSubject == null) return;

    // Clear UI immediately
    setState(() {
      for (final t in scopeTopics) {
        topicProgress[t] = false;
      }
    });

    // Also clear backend file so it stays cleared after refresh
    try {
      await http.post(
        Uri.parse('$serverUrl/subjects/clear_scope_progress'),
        body: {'subject_name': selectedSubject!},
      );
    } catch (_) {}
  }

  // ✅ Visualise dialog player
  void _showVisualiseDialog() {
    showDialog(
      context: context,
      builder: (_) => const _VisualiseVideoDialog(),
    );
  }

  Widget _buildSubjectList() {
    if (savedFiles.isEmpty) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.folder_open, size: 60, color: Colors.grey),
            const SizedBox(height: 10),
            Text("No saved papers for $selectedSubject yet."),
            const SizedBox(height: 20),
            ElevatedButton.icon(
              icon: const Icon(Icons.add),
              label: const Text("Analyze New Paper"),
              onPressed: () => setState(() => activeView = 1),
            )
          ],
        ),
      );
    }

    return Column(
      children: [
        Container(
          padding: const EdgeInsets.all(16),
          color: Colors.blue[50],
          width: double.infinity,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text("Saved Analyses for $selectedSubject",
                  style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
              IconButton(
                icon: const Icon(Icons.add_circle, color: Colors.blue, size: 30),
                onPressed: () => setState(() => activeView = 1),
              )
            ],
          ),
        ),
        Expanded(
          child: ListView.separated(
            itemCount: savedFiles.length,
            separatorBuilder: (c, i) => const Divider(),
            itemBuilder: (context, index) {
              return ListTile(
                leading: const Icon(Icons.description, color: Colors.blue),
                title: Text(savedFiles[index]),
                trailing: const Icon(Icons.arrow_forward_ios, size: 16),
                onTap: () => readFile(savedFiles[index]),
              );
            },
          ),
        ),
      ],
    );
  }

  // ✅ UPDATED UI to match your screenshot style (simple upload + big button)
  Widget _buildUploadView() {
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            IconButton(
              icon: const Icon(Icons.arrow_back),
              onPressed: () => setState(() => activeView = 0),
            ),
            const SizedBox(width: 6),
            const Text(
              "Upload Past Year Paper",
              style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
            ),
          ]),
          const SizedBox(height: 8),
          const Divider(),
          const SizedBox(height: 14),

          // Notes (optional)
          Container(
            width: double.infinity,
            decoration: BoxDecoration(
              color: Colors.grey[100],
              borderRadius: BorderRadius.circular(10),
            ),
            child: ListTile(
              leading: const Icon(Icons.menu_book),
              title: Text(
                notesFiles.isEmpty
                    ? "Select Lecture Notes (PDF) (Optional)"
                    : "Lecture Notes Selected: ${notesFiles.length}",
              ),
              subtitle: notesFiles.isEmpty ? null : Text(notesFiles.map((f) => f.name).join(", ")),
              onTap: () async {
                var r = await FilePicker.platform.pickFiles(
                  type: FileType.custom,
                  allowedExtensions: ['pdf'],
                  withData: true,
                  allowMultiple: true,
                );
                if (!mounted) return;
                if (r != null) setState(() => notesFiles = r.files);
              },
            ),
          ),
          const SizedBox(height: 10),
          SizedBox(
            width: double.infinity,
            height: 48,
            child: ElevatedButton(
              onPressed: isLoading ? null : uploadNotes,
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.indigo,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(24)),
              ),
              child: isLoading
                  ? const CircularProgressIndicator(color: Colors.white)
                  : const Text("UPLOAD NOTES", style: TextStyle(color: Colors.white)),
            ),
          ),

          const SizedBox(height: 20),

          // PYQ (main action)
          Container(
            width: double.infinity,
            decoration: BoxDecoration(
              color: Colors.grey[100],
              borderRadius: BorderRadius.circular(10),
            ),
            child: ListTile(
              leading: const Icon(Icons.upload_file),
              title: Text(
                pyqFiles.isEmpty ? "Select Past Year Question (PDF)" : "PYQ Selected: ${pyqFiles.length}",
              ),
              subtitle: pyqFiles.isEmpty ? null : Text(pyqFiles.map((f) => f.name).join(", ")),
              onTap: () async {
                var r = await FilePicker.platform.pickFiles(
                  type: FileType.custom,
                  allowedExtensions: ['pdf'],
                  withData: true,
                  allowMultiple: true,
                );
                if (!mounted) return;
                if (r != null) setState(() => pyqFiles = r.files);
              },
            ),
          ),
          const SizedBox(height: 18),

          SizedBox(
            width: double.infinity,
            height: 52,
            child: ElevatedButton(
              onPressed: isLoading ? null : uploadPyqAndAnalyze,
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.blue,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(26)),
              ),
              child: isLoading
                  ? const CircularProgressIndicator(color: Colors.white)
                  : const Text("ANALYZE & SAVE", style: TextStyle(color: Colors.white, fontSize: 16)),
            ),
          ),
        ],
      ),
    );
  }

  // ✅ Option 1 reading view: per-question cards + Visualise
  Widget _buildReadingView() {
    if (analysisQuestions.isNotEmpty) {
      return Column(
        children: [
          AppBar(
            leading: IconButton(
              icon: const Icon(Icons.arrow_back),
              onPressed: () => setState(() => activeView = 0),
            ),
            title: const Text("Analysis Result"),
            backgroundColor: Colors.white,
            foregroundColor: Colors.black,
            elevation: 1,
          ),
          Expanded(
            child: ListView.builder(
              itemCount: analysisQuestions.length,
              itemBuilder: (context, index) {
                final q = analysisQuestions[index];
                final qNo = q["question_no"]?.toString() ?? "${index + 1}";
                final qText = q["question"]?.toString() ?? "";
                final qType = (q["type"]?.toString() ?? "subjective").toLowerCase();
                final ans = q["answer"]?.toString() ?? "";
                final opts = q["options"];

                return Card(
                  margin: const EdgeInsets.all(10),
                  child: Padding(
                    padding: const EdgeInsets.all(12),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text("Q$qNo",
                            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                        const SizedBox(height: 8),
                        Text(qText),
                        const SizedBox(height: 10),
                        if (qType == "mcq" && opts is Map) ...[
                          const Text("Options:", style: TextStyle(fontWeight: FontWeight.bold)),
                          const SizedBox(height: 6),
                          for (final k in ["A", "B", "C", "D"])
                            if (opts[k] != null) Text("$k. ${opts[k]}"),
                          const SizedBox(height: 10),
                        ],
                        const Divider(),
                        const Text("Answer:", style: TextStyle(fontWeight: FontWeight.bold)),
                        const SizedBox(height: 6),
                        Text(ans),
                        const SizedBox(height: 12),
                        Align(
                          alignment: Alignment.centerRight,
                          child: ElevatedButton.icon(
                            icon: const Icon(Icons.play_circle),
                            label: const Text("Visualise"),
                            onPressed: _showVisualiseDialog,
                          ),
                        )
                      ],
                    ),
                  ),
                );
              },
            ),
          ),
        ],
      );
    }

    // Fallback: old markdown view
    return Column(
      children: [
        AppBar(
          leading: IconButton(icon: const Icon(Icons.arrow_back), onPressed: () => setState(() => activeView = 0)),
          title: const Text("Analysis Result"),
          backgroundColor: Colors.white,
          foregroundColor: Colors.black,
          elevation: 1,
        ),
        Expanded(
          child: Markdown(data: currentReadingContent),
        ),
      ],
    );
  }

  Widget _buildNotesView() {
    return Column(
      children: [
        AppBar(
          leading: IconButton(icon: const Icon(Icons.arrow_back), onPressed: () => setState(() => activeView = 0)),
          title: Text("Lecture Notes — $selectedSubject"),
          backgroundColor: Colors.white,
          foregroundColor: Colors.black,
          elevation: 1,
        ),
        Expanded(
          child: noteFiles.isEmpty
              ? const Center(child: Text("No lecture notes uploaded yet."))
              : ListView.separated(
                  itemCount: noteFiles.length,
                  separatorBuilder: (_, _) => const Divider(),
                  itemBuilder: (context, index) {
                    final f = noteFiles[index];
                    return ListTile(
                      leading: const Icon(Icons.picture_as_pdf, color: Colors.red),
                      title: Text(f),
                      trailing: const Icon(Icons.open_in_new),
                      onTap: () => openNoteExternally(f),
                    );
                  },
                ),
        )
      ],
    );
  }

  Widget _buildScopeView() {
    if (scopeTopics.isEmpty) {
      return Center(
        child: ElevatedButton(
          onPressed: generateScopeChecklist,
          child: const Text("Generate Scope Analysis"),
        ),
      );
    }

    return Column(
      children: [
        Container(
          padding: const EdgeInsets.all(20),
          color: Colors.indigo[50],
          child: Row(
            children: [
              const Icon(Icons.analytics, color: Colors.indigo),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  "Scope Coverage: $selectedSubject",
                  style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                ),
              ),
              TextButton.icon(
                onPressed: clearAllScopeTicks,
                icon: const Icon(Icons.clear_all),
                label: const Text("Clear All"),
              ),
              IconButton(icon: const Icon(Icons.refresh), onPressed: fetchScope)
            ],
          ),
        ),
        Expanded(
          child: ListView.builder(
            itemCount: scopeTopics.length,
            itemBuilder: (context, index) {
              String topic = scopeTopics[index];
              return CheckboxListTile(
                title: Text(topic),
                subtitle: Text(topicProgress[topic] == true ? "Completed" : "To Study"),
                value: topicProgress[topic] ?? false,
                onChanged: (bool? value) async {
                  final v = value ?? false;

                  // update UI immediately
                  setState(() => topicProgress[topic] = v);

                  // save to backend
                  try {
                    await http.post(
                      Uri.parse('$serverUrl/subjects/toggle_scope_item'),
                      body: {
                        'subject_name': selectedSubject!,
                        'topic_index': index.toString(),
                        'completed': v.toString(),
                      },
                    );
                  } catch (_) {}
                },
              );
            },
          ),
        ),
      ],
    );
  }

  Widget _buildMockExamView() {
    if (mockQuestions.isEmpty) {
      return Center(
        child: ElevatedButton(
          onPressed: generateMockExam,
          child: const Text("Generate Mock Exam"),
        ),
      );
    }

    return ListView.builder(
      itemCount: mockQuestions.length,
      itemBuilder: (context, index) {
        return Card(
          margin: const EdgeInsets.all(8),
          child: ExpansionTile(
            title: Text(mockQuestions[index]['question']),
            children: [
              Padding(
                padding: const EdgeInsets.all(16),
                child: Text(mockQuestions[index]['answer']),
              )
            ],
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text("StudyBuddy AI"),
        backgroundColor: Colors.indigo,
        foregroundColor: Colors.white,
      ),
      drawer: Drawer(
        child: ListView(
          children: [
            const DrawerHeader(
              decoration: BoxDecoration(color: Colors.indigo),
              child: Center(
                child: Text("MY SUBJECTS", style: TextStyle(color: Colors.white, fontSize: 20)),
              ),
            ),
            ...subjects.map((s) => ListTile(
                  title: Text(s, style: const TextStyle(fontSize: 16)),
                  leading: const Icon(Icons.folder),
                  onTap: () {
                    setState(() => selectedSubject = s);
                    Navigator.pop(context);
                    fetchSavedFiles();
                  },
                )),
            ListTile(
              leading: const Icon(Icons.add),
              title: const Text("Create New Subject"),
              onTap: () {
                TextEditingController subjectController = TextEditingController();
                showDialog(
                  context: context,
                  builder: (dialogContext) => AlertDialog(
                    title: const Text("New Subject Name"),
                    content: TextField(controller: subjectController, autofocus: true),
                    actions: [
                      TextButton(onPressed: () => Navigator.pop(dialogContext), child: const Text("Cancel")),
                      ElevatedButton(
                        onPressed: () async {
                          final String newName = subjectController.text.trim();
                          if (newName.isNotEmpty) {
                            Navigator.pop(dialogContext);
                            try {
                              var res = await http.post(
                                Uri.parse('$serverUrl/subjects/create'),
                                body: {'name': newName},
                              );
                              if (!mounted) return;
                              if (res.statusCode == 200) {
                                await fetchSubjects();
                              }
                            } catch (e) {
                              debugPrint("Create subject error: $e");
                            }
                          }
                        },
                        child: const Text("Create"),
                      ),
                    ],
                  ),
                );
              },
            ),
            if (selectedSubject != null) ...[
              const Divider(),
              ListTile(
                leading: const Icon(Icons.folder_open),
                title: const Text("Saved Papers"),
                onTap: () {
                  setState(() => activeView = 0);
                  Navigator.pop(context);
                },
              ),
              ListTile(
                leading: const Icon(Icons.upload_file),
                title: const Text("Analyze New Paper"),
                onTap: () {
                  setState(() => activeView = 1);
                  Navigator.pop(context);
                },
              ),
              ListTile(
                leading: const Icon(Icons.menu_book, color: Colors.teal),
                title: const Text("Lecture Notes"),
                onTap: () {
                  Navigator.pop(context);
                  fetchNotes();
                },
              ),
              ListTile(
                leading: const Icon(Icons.analytics, color: Colors.deepPurple),
                title: const Text("Scope Analysis"),
                onTap: () {
                  setState(() => activeView = 3);
                  Navigator.pop(context);
                  fetchScope();
                },
              ),
              ListTile(
                leading: const Icon(Icons.quiz, color: Colors.orange),
                title: const Text("Mock Exam Generator"),
                onTap: () {
                  setState(() => activeView = 4);
                  Navigator.pop(context);
                },
              ),
            ]
          ],
        ),
      ),
      body: selectedSubject == null
          ? const Center(child: Text("Please select a subject from the sidebar ⬅️"))
          : (isLoading
              ? const Center(child: CircularProgressIndicator())
              : (activeView == 0
                  ? _buildSubjectList()
                  : activeView == 1
                      ? _buildUploadView()
                      : activeView == 2
                          ? _buildReadingView()
                          : activeView == 3
                              ? _buildScopeView()
                              : activeView == 4
                                  ? _buildMockExamView()
                                  : _buildNotesView())),
    );
  }
}

class _VisualiseVideoDialog extends StatefulWidget {
  const _VisualiseVideoDialog();

  @override
  State<_VisualiseVideoDialog> createState() => _VisualiseVideoDialogState();
}

class _VisualiseVideoDialogState extends State<_VisualiseVideoDialog> {
  late final VideoPlayerController _controller;
  late final Future<void> _initFuture;

  @override
  void initState() {
    super.initState();
    // ✅ plays study_app/assets/demo_video.mp4
    _controller = VideoPlayerController.asset('assets/demo_video.mp4');
    _initFuture = _controller.initialize().then((_) {
      _controller.setLooping(true);
      _controller.play();
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text("Visualise"),
      content: FutureBuilder<void>(
        future: _initFuture,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done) {
            return const SizedBox(
              height: 220,
              width: 320,
              child: Center(child: CircularProgressIndicator()),
            );
          }
          if (snapshot.hasError) {
            return SizedBox(
              height: 220,
              width: 320,
              child: Center(child: Text("Video failed: ${snapshot.error}")),
            );
          }
          if (!_controller.value.isInitialized) {
            return const SizedBox(
              height: 220,
              width: 320,
              child: Center(child: Text("Video not initialized")),
            );
          }
          return SizedBox(
            width: 500,
            child: AspectRatio(
              aspectRatio: _controller.value.aspectRatio,
              child: VideoPlayer(_controller),
            ),
          );
        },
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text("Close"),
        ),
      ],
    );
  }
}