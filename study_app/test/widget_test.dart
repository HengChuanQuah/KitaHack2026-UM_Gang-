import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:study_app/main.dart';

void main() {
  testWidgets('HomeScreen builds', (WidgetTester tester) async {
    await tester.pumpWidget(const MaterialApp(home: HomeScreen()));
    expect(find.byType(HomeScreen), findsOneWidget);
  });
}